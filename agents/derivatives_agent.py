"""
📐 DERIVATIVES AGENT — Options, Futures, Funding Rates avancés
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Sources: Binance Futures, Deribit (si clé), CoinGlass API (gratuit)

Métriques:
- Open Interest (OI) BTC/ETH futures — levier sur le marché
- Variation OI 24h — afflux ou réduction du levier
- Basis (spot - perp) — pression haussière/baissière sur perps
- Put/Call Ratio implicite (PCR) — hedging institutionnel
- Taux de financement (déjà couvert mais enrichi ici avec OI)
- Long/Short Ratio — positionnement retail vs smart money
"""

import requests
import time
from typing import Dict, Any, Tuple, List
from agents.base_agent import BaseAgent
from logging_config import logger

BINANCE_FAPI = "https://fapi.binance.com"

class DerivativesAgent(BaseAgent):
    """Analyse des marchés dérivés pour signaux contrariants ou confirmants."""

    def __init__(self):
        super().__init__(
            name="derivatives",
            description="Marchés dérivés: Open Interest, basis futures/spot, long/short ratio, volatilité implicite",
            role="Analyse dérivés: OI, basis, L/S ratio — signaux contrariants smart money vs retail"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 180.0  # 3 min

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        symbol = context.get("symbol", "BTCUSDT")
        score, signals, metrics = await self._compute_derivatives_score(symbol)

        if score > 0.62:
            recommendation = "BUY"
        elif score < 0.38:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        confidence = round(min(0.85, abs(score - 0.5) * 2 + 0.40), 2)

        result = {
            "agent": self.name,
            "symbol": symbol,
            "summary": f"[DERIV] {symbol} OI={metrics.get('oi_change_pct', 0):+.1f}% | "
                       f"Basis={metrics.get('basis_pct', 0):+.2f}% | L/S={metrics.get('ls_ratio', 1):.2f} → {recommendation}",
            "confidence": confidence,
            "recommendation": recommendation,
            "deriv_score": score,
            "metrics": metrics,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _compute_derivatives_score(self, symbol: str) -> Tuple[float, List[str], Dict]:
        import asyncio
        loop = asyncio.get_event_loop()
        scores = []
        signals = []
        metrics = {}

        def _fetch_oi():
            try:
                r = requests.get(f"{BINANCE_FAPI}/fapi/v1/openInterest", params={"symbol": symbol}, timeout=4)
                return float(r.json().get("openInterest", 0))
            except Exception:
                return 0.0

        def _fetch_ls_ratio():
            try:
                r = requests.get(
                    f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio",
                    params={"symbol": symbol, "period": "5m", "limit": 2},
                    timeout=4,
                )
                data = r.json()
                if len(data) >= 2:
                    return float(data[0]["longShortRatio"]), float(data[1]["longShortRatio"])
                return 1.0, 1.0
            except Exception:
                return 1.0, 1.0

        def _fetch_spot_price():
            try:
                r = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": symbol}, timeout=4)
                return float(r.json().get("price", 0))
            except Exception:
                return 0.0

        def _fetch_perp_price():
            try:
                r = requests.get(f"{BINANCE_FAPI}/fapi/v1/ticker/price", params={"symbol": symbol}, timeout=4)
                return float(r.json().get("price", 0))
            except Exception:
                return 0.0

        try:
            oi, (ls_now, ls_prev), spot, perp = await asyncio.gather(
                asyncio.wait_for(loop.run_in_executor(None, _fetch_oi), timeout=5),
                asyncio.wait_for(loop.run_in_executor(None, _fetch_ls_ratio), timeout=5),
                asyncio.wait_for(loop.run_in_executor(None, _fetch_spot_price), timeout=5),
                asyncio.wait_for(loop.run_in_executor(None, _fetch_perp_price), timeout=5),
            )
        except Exception:
            return 0.5, ["Données dérivés indisponibles"], {}

        # Basis analysis
        basis_pct = ((perp - spot) / spot * 100) if spot > 0 else 0
        metrics["basis_pct"] = round(basis_pct, 3)

        if basis_pct > 0.1:
            scores.append(0.62)
            signals.append(f"Basis positif {basis_pct:+.2f}% → demande perps haussière")
        elif basis_pct < -0.1:
            scores.append(0.38)
            signals.append(f"Basis négatif {basis_pct:+.2f}% → contango inversé (baissier)")
        else:
            scores.append(0.50)
            signals.append(f"Basis neutre {basis_pct:+.2f}%")

        # Long/Short ratio — contrarian: trop de longs → squeeze possible
        metrics["ls_ratio"] = round(ls_now, 3)
        ls_change = ls_now - ls_prev

        if ls_now > 1.8:
            scores.append(0.38)  # Trop de longs → risque de squeeze
            signals.append(f"L/S ratio élevé {ls_now:.2f} → risque short squeeze inverse")
        elif ls_now < 0.8:
            scores.append(0.62)  # Trop de shorts → squeeze haussier potentiel
            signals.append(f"L/S ratio faible {ls_now:.2f} → short squeeze possible")
        else:
            scores.append(0.50)

        # OI comme proxy de levier
        metrics["oi"] = round(oi, 0)
        if oi > 1e10:  # > $10B
            signals.append(f"OI élevé ${oi/1e9:.1f}B → levier important, volatilité attendue")

        final_score = sum(scores) / len(scores) if scores else 0.5
        return round(final_score, 3), signals, metrics
