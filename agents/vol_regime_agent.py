"""
📊 VOL REGIME AGENT — Régime de volatilité (low/high vol)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
La volatilité est le signal le plus persistant en finance.
- Vol regime BAS (< 30% ann.) → tendances plus fiables → stratégie trend following
- Vol regime HAUT (> 60% ann.) → mean-reverting → stratégie contrarian

Calcule la volatilité réalisée (HV) sur différentes fenêtres:
- HV5 / HV20 / HV60
- Ratio HV5/HV60 → détection de spike de vol
- Vol cone: percentile historique de la vol actuelle
"""

import requests
import numpy as np
import time
from typing import Dict, Any, Tuple, List
from agents.base_agent import BaseAgent
from logging_config import logger

BINANCE_BASE = "https://api.binance.com"

class VolRegimeAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="vol_regime",
            description="Régime de volatilité: HV5/HV20/HV60, vol cone, détection spike — adaptation stratégie",
            role="Vol regime: low vol → trend following, high vol → mean reversion, spike → prudence"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 300.0

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        symbol = context.get("symbol", "BTCUSDT")
        score, signals, metrics = await self._compute_vol_regime(symbol)

        if score > 0.60:
            recommendation = "BUY"
        elif score < 0.40:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        confidence = round(min(0.80, abs(score - 0.5) * 2 + 0.35), 2)

        result = {
            "agent": self.name,
            "symbol": symbol,
            "summary": (f"[VOL REGIME] HV5={metrics.get('hv5',0):.0f}% | "
                        f"HV20={metrics.get('hv20',0):.0f}% | "
                        f"Régime={metrics.get('regime','?')} → {recommendation}"),
            "confidence": confidence,
            "recommendation": recommendation,
            "vol_score": score,
            "metrics": metrics,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _compute_vol_regime(self, symbol: str) -> Tuple[float, List[str], Dict]:
        import asyncio
        loop = asyncio.get_event_loop()

        def _fetch_daily_closes(limit=65):
            try:
                r = requests.get(
                    f"{BINANCE_BASE}/api/v3/klines",
                    params={"symbol": symbol, "interval": "1d", "limit": limit},
                    timeout=5,
                )
                return [float(k[4]) for k in r.json()]
            except Exception:
                return []

        try:
            closes = await asyncio.wait_for(
                loop.run_in_executor(None, _fetch_daily_closes), timeout=6
            )
        except Exception:
            return 0.5, ["Données vol indisponibles"], {}

        if len(closes) < 20:
            return 0.5, ["Données insuffisantes"], {}

        closes = np.array(closes)
        returns = np.diff(np.log(closes))

        def hv(n):
            if len(returns) < n:
                return 0.0
            return float(np.std(returns[-n:]) * np.sqrt(365) * 100)

        hv5 = hv(5)
        hv20 = hv(20)
        hv60 = hv(60) if len(returns) >= 60 else hv20

        metrics = {
            "hv5": round(hv5, 1),
            "hv20": round(hv20, 1),
            "hv60": round(hv60, 1),
            "vol_ratio": round(hv5 / (hv60 + 0.01), 2),
        }

        scores = []
        signals = []

        # Régime
        if hv20 < 30:
            regime = "LOW VOL"
            scores.append(0.60)  # Tendances plus fiables → légèrement haussier
            signals.append(f"Vol basse ({hv20:.0f}%) → trend following efficace")
        elif hv20 > 70:
            regime = "HIGH VOL"
            scores.append(0.42)  # Volatilité dangereuse → prudence
            signals.append(f"Vol élevée ({hv20:.0f}%) → réduire position sizing")
        else:
            regime = "MEDIUM VOL"
            scores.append(0.50)
            signals.append(f"Vol normale ({hv20:.0f}%)")

        metrics["regime"] = regime

        # Spike de vol
        vol_ratio = hv5 / (hv60 + 0.01)
        if vol_ratio > 2.0:
            scores.append(0.38)
            signals.append(f"Spike vol: HV5/HV60={vol_ratio:.1f}x → risque accru, prudence")
        elif vol_ratio < 0.5:
            scores.append(0.62)
            signals.append(f"Vol comprimée: HV5/HV60={vol_ratio:.1f}x → breakout potentiel")

        final_score = sum(scores) / len(scores) if scores else 0.5
        return round(final_score, 3), signals, metrics
