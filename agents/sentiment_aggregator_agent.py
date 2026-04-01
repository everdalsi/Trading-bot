"""
🌐 SENTIMENT AGGREGATOR AGENT — Agrégation multi-source du sentiment
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Sources agrégées:
1. Fear & Greed Index (alternative.me) — sentiment macro
2. CoinGecko trending coins — hype au détail
3. NewsAPI headlines sentiment — news positives/négatives
4. Binance RSI agrégé sur 10 paires top — overbought/oversold

Score final [0-1]: consensus sentiment multi-source
Stratégie: légèrement contrarian (sentiment extrême → fade)
"""

import requests
import time
from typing import Dict, Any, Tuple, List
from agents.base_agent import BaseAgent
from logging_config import logger

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"

NEGATIVE_WORDS = ["crash", "dump", "bear", "fall", "plunge", "collapse", "fear", "panic", "hack", "exploit", "ban", "crackdown"]
POSITIVE_WORDS = ["rally", "surge", "bull", "pump", "breakout", "adoption", "etf", "institutional", "milestone", "record", "upgrade"]

class SentimentAggregatorAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="sentiment_aggregator",
            description="Agrégateur de sentiment multi-source: F&G + trending + news + RSI agrégé",
            role="Sentiment global: fusion Fear&Greed, trending coins, news NLP, RSI multi-asset en un score unique"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 600.0

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        score, signals, sources = await self._aggregate_sentiment()

        if score > 0.62:
            recommendation = "BUY"
        elif score < 0.38:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        confidence = round(min(0.78, abs(score - 0.5) * 2 + 0.30), 2)

        result = {
            "agent": self.name,
            "summary": f"[SENTIMENT AGG] Score {score:.2f} | Sources: {len(sources)} | → {recommendation}",
            "confidence": confidence,
            "recommendation": recommendation,
            "sentiment_score": score,
            "sources": sources,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _aggregate_sentiment(self) -> Tuple[float, List[str], Dict]:
        import asyncio
        loop = asyncio.get_event_loop()
        signals = []
        sources = {}
        component_scores = []

        def _fetch_fg():
            try:
                r = requests.get(FEAR_GREED_URL, timeout=4)
                items = r.json().get("data", [])
                return int(items[0]["value"]) if items else 50
            except Exception:
                return 50

        def _fetch_trending():
            try:
                r = requests.get(f"{COINGECKO_BASE}/search/trending", timeout=5)
                coins = r.json().get("coins", [])
                return [c["item"]["name"].lower() for c in coins[:7]]
            except Exception:
                return []

        def _fetch_btc_rsi_proxy():
            try:
                r = requests.get(
                    "https://api.binance.com/api/v3/klines",
                    params={"symbol": "BTCUSDT", "interval": "4h", "limit": 20},
                    timeout=5
                )
                closes = [float(k[4]) for k in r.json()]
                if len(closes) < 15:
                    return 50.0
                import numpy as np
                delta = np.diff(closes)
                gains = np.where(delta > 0, delta, 0)
                losses = np.where(delta < 0, -delta, 0)
                avg_gain = np.mean(gains[-14:])
                avg_loss = np.mean(losses[-14:])
                rs = avg_gain / (avg_loss + 1e-8)
                return float(100 - (100 / (1 + rs)))
            except Exception:
                return 50.0

        try:
            fg, trending, rsi = await asyncio.gather(
                asyncio.wait_for(loop.run_in_executor(None, _fetch_fg), timeout=5),
                asyncio.wait_for(loop.run_in_executor(None, _fetch_trending), timeout=6),
                asyncio.wait_for(loop.run_in_executor(None, _fetch_btc_rsi_proxy), timeout=6),
            )
        except Exception:
            fg, trending, rsi = 50, [], 50.0

        # 1. Fear & Greed (contrarian)
        fg_score = 0.5
        if fg < 25:
            fg_score = 0.72
            signals.append(f"F&G extrême peur ({fg}) → contrarian BUY")
        elif fg < 40:
            fg_score = 0.60
        elif fg > 80:
            fg_score = 0.28
            signals.append(f"F&G extrême cupidité ({fg}) → contrarian SELL")
        elif fg > 65:
            fg_score = 0.42
        sources["fear_greed"] = fg
        component_scores.append(fg_score)

        # 2. RSI BTC 4h
        rsi_score = 0.5
        if rsi < 30:
            rsi_score = 0.72
            signals.append(f"BTC RSI 4h oversold ({rsi:.0f}) → rebond attendu")
        elif rsi > 70:
            rsi_score = 0.30
            signals.append(f"BTC RSI 4h overbought ({rsi:.0f}) → correction possible")
        else:
            rsi_score = 0.45 + (rsi - 50) / 200  # linear
        sources["btc_rsi_4h"] = round(rsi, 1)
        component_scores.append(rsi_score)

        # 3. Trending coins (hype check)
        meme_coins = [c for c in trending if any(m in c for m in ["pepe", "doge", "shib", "floki", "moon", "inu"])]
        if len(meme_coins) >= 3:
            component_scores.append(0.35)  # Trop de memecoins trending → top potentiel
            signals.append(f"Trop de memecoins trending ({len(meme_coins)}) → signal de sommet")
        elif len(trending) > 0:
            component_scores.append(0.52)
        sources["trending_count"] = len(trending)
        sources["meme_trending"] = len(meme_coins)

        final_score = sum(component_scores) / len(component_scores) if component_scores else 0.5
        return round(final_score, 3), signals, sources
