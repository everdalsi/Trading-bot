"""
🐋 WHALE TRACKER AGENT — Surveillance des grosses transactions
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Les whales (>$500K transaction) donnent des signaux directionnels.
- Whale achetant sur spot → accumulation → haussier
- Whale transférant vers exchange → vente imminente → baissier
- Whale liquidant positions futures → panic → opportunity

Source: Binance large trades API + blockchain mempool filters
"""

import requests
import time
from typing import Dict, Any, Tuple, List
from agents.base_agent import BaseAgent
from logging_config import logger

BINANCE_BASE = "https://api.binance.com"

class WhaleTrackerAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="whale_tracker",
            description="Surveillance grosses transactions (>$500K): accumulation vs distribution des whales",
            role="Whale tracking: détection transactions majeures, ratio buy/sell whale pour signal directionnel"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 120.0  # 2 min

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        symbol = context.get("symbol", "BTCUSDT")
        score, signals, metrics = await self._track_whales(symbol)

        if score > 0.62:
            recommendation = "BUY"
        elif score < 0.38:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        confidence = round(min(0.82, abs(score - 0.5) * 2 + 0.38), 2)

        result = {
            "agent": self.name,
            "symbol": symbol,
            "summary": (f"[WHALE] {symbol} | Whale buys: ${metrics.get('whale_buy_vol',0)/1e6:.1f}M | "
                        f"Whale sells: ${metrics.get('whale_sell_vol',0)/1e6:.1f}M → {recommendation}"),
            "confidence": confidence,
            "recommendation": recommendation,
            "whale_score": score,
            "metrics": metrics,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _track_whales(self, symbol: str) -> Tuple[float, List[str], Dict]:
        import asyncio
        loop = asyncio.get_event_loop()
        signals = []
        metrics = {}

        def _fetch_agg_trades():
            try:
                r = requests.get(
                    f"{BINANCE_BASE}/api/v3/aggTrades",
                    params={"symbol": symbol, "limit": 500},
                    timeout=5,
                )
                return r.json() if isinstance(r.json(), list) else []
            except Exception:
                return []

        def _fetch_price():
            try:
                r = requests.get(
                    f"{BINANCE_BASE}/api/v3/ticker/price",
                    params={"symbol": symbol},
                    timeout=4
                )
                return float(r.json().get("price", 0))
            except Exception:
                return 0.0

        try:
            trades, price = await asyncio.gather(
                asyncio.wait_for(loop.run_in_executor(None, _fetch_agg_trades), timeout=6),
                asyncio.wait_for(loop.run_in_executor(None, _fetch_price), timeout=4),
            )
        except Exception:
            return 0.5, ["Données whale indisponibles"], {}

        WHALE_THRESHOLD = 500_000  # $500K minimum
        whale_buy = 0.0
        whale_sell = 0.0
        whale_count_buy = 0
        whale_count_sell = 0

        for trade in trades:
            try:
                qty = float(trade.get("q", 0))
                value = qty * price
                is_maker = trade.get("m", False)  # maker = sell
                if value >= WHALE_THRESHOLD:
                    if is_maker:
                        whale_sell += value
                        whale_count_sell += 1
                    else:
                        whale_buy += value
                        whale_count_buy += 1
            except Exception:
                pass

        metrics["whale_buy_vol"] = round(whale_buy, 0)
        metrics["whale_sell_vol"] = round(whale_sell, 0)
        metrics["whale_count_buy"] = whale_count_buy
        metrics["whale_count_sell"] = whale_count_sell

        total = whale_buy + whale_sell
        if total < 1:
            return 0.50, ["Aucune transaction whale détectée récemment"], metrics

        buy_ratio = whale_buy / total
        metrics["whale_buy_ratio"] = round(buy_ratio, 3)

        if buy_ratio > 0.65:
            score = 0.68
            signals.append(f"Whales achètent massivement: {buy_ratio:.0%} du volume whale ({whale_count_buy} trades)")
        elif buy_ratio < 0.35:
            score = 0.32
            signals.append(f"Whales vendent massivement: {1-buy_ratio:.0%} du volume whale ({whale_count_sell} trades)")
        elif buy_ratio > 0.55:
            score = 0.57
            signals.append(f"Légère accumulation whale ({buy_ratio:.0%})")
        elif buy_ratio < 0.45:
            score = 0.43
            signals.append(f"Légère distribution whale ({1-buy_ratio:.0%})")
        else:
            score = 0.50
            signals.append(f"Whales équilibrés: {buy_ratio:.0%} buys / {1-buy_ratio:.0%} sells")

        return round(score, 3), signals, metrics
