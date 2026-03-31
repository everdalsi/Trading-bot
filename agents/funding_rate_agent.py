"""
💸 FUNDING RATE AGENT V1.0 — Surveillance taux de financement futures perp
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Rôle : Surveille le funding rate des futures perpétuels Binance.
       Quand funding > +0.05% → les longs paient énormément → correction imminente.
       Veto automatique sur les nouvelles positions LONG en funding extrême.
Priorité : MOYENNE
"""

import time
import asyncio
import requests
from typing import Dict, Any, List, Optional

from agents.base_agent import BaseAgent
from logging_config import logger

# Seuils funding rate (par 8h)
FUNDING_EXTREME_LONG  = 0.0005   # +0.05% → longs paient énormément → veto LONG
FUNDING_HIGH_LONG     = 0.0003   # +0.03% → longs coûteux → réduire taille
FUNDING_EXTREME_SHORT = -0.0003  # -0.03% → shorts paient → signal LONG favorable
FUNDING_NEUTRAL_LOW   = 0.0001   # Neutre entre -0.01% et +0.01%
FUNDING_NEUTRAL_HIGH  = -0.0001

BINANCE_BASE = "https://fapi.binance.com"
CACHE_TTL    = 480.0   # 8 min (aligné sur le cycle de calcul Binance = 8h)


class FundingRateAgent(BaseAgent):
    """
    Surveille le taux de financement des futures perpétuels.
    Un funding élevé signale un marché suracheté (longs payent les shorts).
    Veto des nouvelles positions LONG quand funding > 0.05%.
    """

    def __init__(self):
        super().__init__(
            name="funding_rate",
            role=(
                "Surveillance funding rate futures perp Binance — "
                "veto LONG si funding > +0.05%, signal favorable si funding < -0.03%"
            )
        )
        self._cache: Dict[str, dict]     = {}
        self._cache_ts: Dict[str, float] = {}
        # Watchlist multi-symboles
        self._watchlist = [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
            "ADAUSDT", "XRPUSDT", "DOGEUSDT", "AVAXUSDT",
        ]

    # ── Domaine ────────────────────────────────────────────────────────────
    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        return any(kw in q for kw in [
            "funding", "financement", "perp", "perpetual",
            "futures", "funding_rate", "taux",
        ]) or super()._is_in_my_domain(question)

    # ── Fetch funding rate ──────────────────────────────────────────────────
    def _fetch_funding_rate(self, symbol: str) -> Optional[float]:
        """Récupère le funding rate actuel depuis Binance Futures."""
        now = time.time()
        sym = symbol.upper()

        if sym in self._cache and now - self._cache_ts.get(sym, 0) < CACHE_TTL:
            return self._cache[sym].get("funding_rate")

        try:
            url    = f"{BINANCE_BASE}/fapi/v1/premiumIndex"
            params = {"symbol": sym}
            resp   = requests.get(url, params=params, timeout=4)  # BUG FIX: était 6s > PHASE0_TIMEOUT=5s
            if resp.status_code != 200:
                return None
            data          = resp.json()
            funding_rate  = float(data.get("lastFundingRate", 0))
            mark_price    = float(data.get("markPrice", 0))
            index_price   = float(data.get("indexPrice", 0))
            self._cache[sym]    = {
                "funding_rate": funding_rate,
                "mark_price":   mark_price,
                "index_price":  index_price,
            }
            self._cache_ts[sym] = now
            return funding_rate
        except Exception as e:
            logger.warning(f"[FUNDING_RATE] Fetch error {sym}: {e}")
            return None

    def _fetch_all_watchlist(self) -> Dict[str, float]:
        """Récupère tous les funding rates de la watchlist d'un coup."""
        try:
            url  = f"{BINANCE_BASE}/fapi/v1/premiumIndex"
            resp = requests.get(url, timeout=4)  # BUG FIX: était 8s > PHASE0_TIMEOUT=5s
            if resp.status_code != 200:
                return {}
            all_data = resp.json()
            result   = {}
            for item in all_data:
                sym = item.get("symbol", "")
                if sym in self._watchlist:
                    result[sym] = float(item.get("lastFundingRate", 0))
            return result
        except Exception as e:
            logger.warning(f"[FUNDING_RATE] Watchlist fetch error: {e}")
            return {}

    # ── Analyse funding ─────────────────────────────────────────────────────
    def _classify_funding(self, rate: float) -> str:
        if rate >= FUNDING_EXTREME_LONG:
            return "EXTREME_LONG"
        elif rate >= FUNDING_HIGH_LONG:
            return "HIGH_LONG"
        elif rate <= FUNDING_EXTREME_SHORT:
            return "EXTREME_SHORT"
        elif FUNDING_NEUTRAL_HIGH <= rate <= FUNDING_NEUTRAL_LOW:
            return "NEUTRAL"
        else:
            return "NORMAL"

    # ── Respond ─────────────────────────────────────────────────────────────
    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        symbol   = context.get("symbol", "BTCUSDT").upper()
        side     = context.get("side", "LONG").upper()

        if not symbol.endswith("USDT"):
            symbol = symbol.replace("/", "") + "USDT"

        loop = asyncio.get_event_loop()
        funding = await loop.run_in_executor(None, lambda: self._fetch_funding_rate(symbol))

        if funding is None:
            # Spot market — pas de funding
            return {
                "agent":          self.name,
                "summary":        f"ℹ️ Funding rate indisponible pour {symbol} (Spot ou erreur API)",
                "confidence":     0.0,
                "recommendation": "HOLD - Données funding manquantes",
                "funding_rate":   0.0,
                "classification": "UNKNOWN",
            }

        classification = self._classify_funding(funding)
        funding_pct    = funding * 100

        # ── Veto LONG en funding extrême ────────────────────────────────────
        if classification == "EXTREME_LONG" and side in ("LONG", "BUY"):
            return {
                "agent":          self.name,
                "summary":        (
                    f"🛑 VETO LONG — Funding extrême {symbol}: {funding_pct:.4f}% "
                    f"(longs paient +{funding_pct:.3f}%/8h — correction imminente)"
                ),
                "arguments": [
                    f"Funding rate: {funding_pct:.4f}% (seuil critique: +0.05%)",
                    "Longs paient massivement les shorts → liquidations probables",
                    "Historique: funding >0.05% précède une correction dans 70% des cas",
                ],
                "risks":          ["Liquidation cascade imminente", "Funding drag important"],
                "confidence":     0.88,
                "recommendation": f"NO TRADE LONG — Attendre funding < +0.03% | Actuel: {funding_pct:.4f}%",
                "veto":           True,
                "veto_reason":    "extreme_funding_long",
                "funding_rate":   funding,
                "classification": classification,
            }

        # ── Réduction taille en funding élevé ───────────────────────────────
        if classification == "HIGH_LONG" and side in ("LONG", "BUY"):
            return {
                "agent":          self.name,
                "summary":        f"⚠️ Funding élevé {symbol}: {funding_pct:.4f}% — réduire taille LONG de 40%",
                "arguments":      [f"Funding {funding_pct:.4f}% > seuil normal (+0.01%) — coût élevé"],
                "risks":          ["Coût funding élevé", "Risque correction"],
                "confidence":     0.65,
                "recommendation": "TRADE RÉDUIT — réduire taille de 40%",
                "veto":           False,
                "size_reduction": 0.40,
                "funding_rate":   funding,
                "classification": classification,
            }

        # ── Signal favorable pour LONG (shorts paient) ─────────────────────
        if classification == "EXTREME_SHORT":
            return {
                "agent":          self.name,
                "summary":        f"✅ Funding favorable {symbol}: {funding_pct:.4f}% — shorts paient → BUY avantageux",
                "arguments":      ["Funding négatif = shorts surreprésentés → retour à la moyenne probable"],
                "risks":          [],
                "confidence":     0.72,
                "recommendation": "BUY favorisé — funding négatif = tailwind LONG",
                "veto":           False,
                "funding_rate":   funding,
                "classification": classification,
            }

        # ── Cas neutre / normal ─────────────────────────────────────────────
        return {
            "agent":          self.name,
            "summary":        f"✅ Funding {symbol}: {funding_pct:.4f}% ({classification}) — conditions normales",
            "arguments":      [f"Funding dans la plage normale (seuil: ±0.03%)"],
            "risks":          [],
            "confidence":     0.6,
            "recommendation": "TRADE AUTORISÉ — funding normal",
            "veto":           False,
            "funding_rate":   funding,
            "classification": classification,
        }

    # ── API publique ────────────────────────────────────────────────────────
    def get_funding_rate(self, symbol: str) -> float:
        return self._fetch_funding_rate(symbol) or 0.0

    def is_long_vetoed(self, symbol: str) -> bool:
        rate = self.get_funding_rate(symbol)
        return rate >= FUNDING_EXTREME_LONG

    def get_market_overview(self) -> Dict[str, float]:
        """Vue d'ensemble du funding sur la watchlist."""
        return self._fetch_all_watchlist()
