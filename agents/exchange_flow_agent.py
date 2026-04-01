"""
🏦 EXCHANGE FLOW AGENT — Flux entrants/sortants exchanges
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Principe fondamental:
- BTC entrant sur exchange (inflow)  → intention de vendre → baissier
- BTC sortant des exchanges (outflow) → hodling, DeFi, cold storage → haussier
- Spike d'inflow massif → pression vendeuse immédiate

Source: Blockchain.info + CoinGecko exchange volumes
Proxy: variation du volume exchange vs OI futures
"""

import requests
import time
from typing import Dict, Any, Tuple, List
from agents.base_agent import BaseAgent
from logging_config import logger

BINANCE_BASE = "https://api.binance.com"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

class ExchangeFlowAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="exchange_flow",
            description="Flux exchange: inflow = pression vendeuse, outflow = hodling fort",
            role="Exchange flow analysis: détection pression vendeuse (inflow) ou accumulation (outflow)"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 300.0

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        score, signals, metrics = await self._analyze_exchange_flows()

        if score > 0.60:
            recommendation = "BUY"
        elif score < 0.40:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        confidence = round(min(0.75, abs(score - 0.5) * 2 + 0.30), 2)

        result = {
            "agent": self.name,
            "summary": f"[EX FLOW] Score {score:.2f} → {recommendation} | {signals[0] if signals else ''}",
            "confidence": confidence,
            "recommendation": recommendation,
            "flow_score": score,
            "metrics": metrics,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _analyze_exchange_flows(self) -> Tuple[float, List[str], Dict]:
        import asyncio
        loop = asyncio.get_event_loop()
        scores = []
        signals = []
        metrics = {}

        def _fetch_btc_ticker():
            try:
                # Volume BTC sur Binance (proxy d'activité exchange)
                r = requests.get(
                    f"{BINANCE_BASE}/api/v3/ticker/24hr",
                    params={"symbol": "BTCUSDT"},
                    timeout=4,
                )
                return r.json()
            except Exception:
                return {}

        def _fetch_cg_btc():
            try:
                r = requests.get(
                    f"{COINGECKO_BASE}/coins/bitcoin",
                    params={"localization": "false", "tickers": "false", "community_data": "false"},
                    timeout=5,
                )
                return r.json()
            except Exception:
                return {}

        try:
            btc_ticker, cg_data = await asyncio.gather(
                asyncio.wait_for(loop.run_in_executor(None, _fetch_btc_ticker), timeout=5),
                asyncio.wait_for(loop.run_in_executor(None, _fetch_cg_btc), timeout=6),
            )
        except Exception:
            return 0.5, ["Données indisponibles"], {}

        # Analyse volume Binance
        vol_24h = float(btc_ticker.get("quoteVolume", 0))
        count = int(btc_ticker.get("count", 0))
        price_change = float(btc_ticker.get("priceChangePercent", 0))
        maker_vol = float(btc_ticker.get("takerBuyQuoteVolume", 0))

        metrics["vol_24h_usd"] = round(vol_24h, 0)
        metrics["taker_buy_ratio"] = round(maker_vol / (vol_24h + 1), 3)

        taker_ratio = maker_vol / (vol_24h + 1)
        if taker_ratio > 0.55:
            scores.append(0.63)
            signals.append(f"Taker buy ratio élevé {taker_ratio:.1%} → pression acheteuse")
        elif taker_ratio < 0.45:
            scores.append(0.37)
            signals.append(f"Taker buy ratio faible {taker_ratio:.1%} → pression vendeuse")
        else:
            scores.append(0.50)
            signals.append(f"Taker ratio neutre {taker_ratio:.1%}")

        # CoinGecko: exchange reserve proxy
        market_data = cg_data.get("market_data", {})
        vol_vs_mcap = market_data.get("total_volume", {}).get("usd", 0) / (market_data.get("market_cap", {}).get("usd", 1) + 1)
        metrics["vol_mcap_ratio"] = round(vol_vs_mcap, 4)

        if vol_vs_mcap > 0.10:
            scores.append(0.40)  # Volume très élevé vs mcap → distribution possible
            signals.append(f"Vol/MCap élevé {vol_vs_mcap:.1%} → potentielle distribution")
        elif vol_vs_mcap < 0.03:
            scores.append(0.60)  # Volume faible → accumulation silencieuse
            signals.append(f"Vol/MCap faible {vol_vs_mcap:.1%} → accumulation possible")
        else:
            scores.append(0.50)

        final_score = sum(scores) / len(scores) if scores else 0.5
        return round(final_score, 3), signals, metrics
