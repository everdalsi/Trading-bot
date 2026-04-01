"""
💥 LIQUIDATION TRACKER AGENT — Cascades de liquidations
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Les liquidations en cascade créent des opportunités de trading:
- Long liquidations massives → spike baissier → rebond potentiel
- Short liquidations → short squeeze → entrée haussière

Sources: Binance liquidation stream + Coinglass API (gratuit)
Stratégie: contrarian — acheter après liq longs massives si support tenu
"""

import requests
import time
from typing import Dict, Any, Tuple, List
from agents.base_agent import BaseAgent
from logging_config import logger

BINANCE_FAPI = "https://fapi.binance.com"

class LiquidationTrackerAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="liquidation_tracker",
            description="Suivi des liquidations en cascade — signaux contrariants après squeeze",
            role="Liquidations: détection cascades long/short, opportunités rebond post-liquidation"
        )
        self._recent_liqs: List[Dict] = []
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 120.0

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        symbol = context.get("symbol", "BTCUSDT")
        score, signals, metrics = await self._analyze_liquidations(symbol)

        if score > 0.62:
            recommendation = "BUY"
        elif score < 0.38:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        confidence = round(min(0.80, abs(score - 0.5) * 2 + 0.35), 2)

        result = {
            "agent": self.name,
            "symbol": symbol,
            "summary": f"[LIQ TRACKER] {symbol} | Long liq: ${metrics.get('long_liq_1h', 0)/1e6:.1f}M "
                       f"| Short liq: ${metrics.get('short_liq_1h', 0)/1e6:.1f}M → {recommendation}",
            "confidence": confidence,
            "recommendation": recommendation,
            "liq_score": score,
            "metrics": metrics,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _analyze_liquidations(self, symbol: str) -> Tuple[float, List[str], Dict]:
        import asyncio
        loop = asyncio.get_event_loop()
        scores = []
        signals = []
        metrics = {}

        def _fetch_liq_orders():
            try:
                # Binance force liquidation orders endpoint
                r = requests.get(
                    f"{BINANCE_FAPI}/fapi/v1/allForceOrders",
                    params={"symbol": symbol, "limit": 100},
                    timeout=5,
                )
                return r.json() if isinstance(r.json(), list) else []
            except Exception:
                return []

        try:
            liq_orders = await asyncio.wait_for(
                loop.run_in_executor(None, _fetch_liq_orders), timeout=6
            )
        except Exception:
            liq_orders = []

        # Agréger les liquidations par side
        long_liq_vol = 0.0
        short_liq_vol = 0.0
        now_ms = time.time() * 1000
        cutoff_1h = now_ms - 3600_000

        for order in liq_orders:
            try:
                ts = float(order.get("time", 0))
                qty = float(order.get("origQty", 0))
                price = float(order.get("price", 0))
                value = qty * price
                side = order.get("side", "")
                if ts > cutoff_1h:
                    if side == "SELL":  # Long forced-sell = long liquidation
                        long_liq_vol += value
                    elif side == "BUY":  # Short force-buy = short liquidation
                        short_liq_vol += value
            except Exception:
                pass

        metrics["long_liq_1h"] = round(long_liq_vol, 0)
        metrics["short_liq_1h"] = round(short_liq_vol, 0)
        metrics["liq_ratio"] = round(long_liq_vol / (short_liq_vol + 1), 2)

        # Signal contrarian: si beaucoup de longs liquidés → potentiel rebond
        if long_liq_vol > 50_000_000:  # > $50M
            scores.append(0.65)  # Contrarian buy après liq massive
            signals.append(f"Liq LONG massive: ${long_liq_vol/1e6:.0f}M en 1h → rebond contrarian")
        elif short_liq_vol > 50_000_000:
            scores.append(0.35)  # Short squeeze passé → potentiellement épuisé
            signals.append(f"Liq SHORT massive: ${short_liq_vol/1e6:.0f}M → squeeze potentiellement terminé")
        elif long_liq_vol > 10_000_000:
            scores.append(0.55)
            signals.append(f"Liq LONG modérées: ${long_liq_vol/1e6:.0f}M")
        elif short_liq_vol > 10_000_000:
            scores.append(0.48)
            signals.append(f"Liq SHORT modérées: ${short_liq_vol/1e6:.0f}M")
        else:
            scores.append(0.50)
            signals.append("Liquidations normales — pas de cascade")

        final_score = sum(scores) / len(scores) if scores else 0.5
        return round(final_score, 3), signals, metrics
