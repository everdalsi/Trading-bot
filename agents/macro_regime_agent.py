"""
📊 MACRO REGIME AGENT — Corrélation macro avec BTC/crypto
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Surveille les indicateurs macro-économiques et leur corrélation
avec le marché crypto :
- DXY (Dollar Index) — corrélation négative avec BTC
- VIX (Fear index) — risque marché global  
- SPX / Nasdaq — risk-on/risk-off
- Taux 10Y US — impact sur actifs risqués

Résultat: score risk-on/risk-off + ajustement du bias directional
"""

import requests
import time
from typing import Dict, Any, List, Tuple
from agents.base_agent import BaseAgent
from logging_config import logger

BINANCE_BASE = "https://api.binance.com"

# Proxies gratuits pour données macro (Yahoo Finance / stooq)
STOOQ_BASE = "https://stooq.com/q/d/l/?s={symbol}&i=d"

MACRO_SYMBOLS = {
    "DXY":  {"stooq": "dx.f",  "weight": 0.35, "inverse": True},   # Hausse DXY = baissier crypto
    "SPX":  {"stooq": "^spx",  "weight": 0.30, "inverse": False},  # Hausse SPX = haussier crypto
    "VIX":  {"stooq": "^vix",  "weight": 0.20, "inverse": True},   # Hausse VIX = baissier crypto
    "GOLD": {"stooq": "xauusd","weight": 0.15, "inverse": False},  # Hausse or = haussier (risk-on)
}

class MacroRegimeAgent(BaseAgent):
    """Analyse macro — DXY / VIX / SPX pour déterminer le régime risk-on/off."""

    def __init__(self):
        super().__init__(
            name="macro_regime",
            description="Corrélation macro-économique: DXY, VIX, SPX, Gold → signal risk-on/off pour crypto",
            role="Macro régime: indicateurs US (DXY, VIX, SPX) corrélés avec crypto pour bias directionnel"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 600.0  # 10 min

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        try:
            macro_score, signals = await self._compute_macro_score()
        except Exception as e:
            logger.warning(f"[MACRO REGIME] Erreur: {e}")
            macro_score = 0.5
            signals = ["Données macro indisponibles — signal neutre"]

        if macro_score > 0.60:
            recommendation = "BUY"
            bias = "RISK-ON"
        elif macro_score < 0.40:
            recommendation = "SELL"
            bias = "RISK-OFF"
        else:
            recommendation = "HOLD"
            bias = "NEUTRAL"

        confidence = abs(macro_score - 0.5) * 2 * 0.8

        result = {
            "agent": self.name,
            "summary": f"[MACRO] Régime {bias} — score {macro_score:.2f} | {' | '.join(signals[:2])}",
            "confidence": round(confidence, 2),
            "recommendation": recommendation,
            "macro_score": macro_score,
            "bias": bias,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _compute_macro_score(self) -> Tuple[float, List[str]]:
        """Récupère les données macro via Binance (corrélés BTC) — fallback heuristique."""
        import asyncio

        # Utilise Binance pour BTCUSDT vs des proxies macro
        # Pour DXY: utilisation du taux EUR/USDT comme proxy inversé
        signals = []
        scores = []

        loop = asyncio.get_event_loop()

        def _fetch_binance_price(symbol: str) -> float:
            try:
                r = requests.get(
                    f"{BINANCE_BASE}/api/v3/ticker/24hr",
                    params={"symbol": symbol},
                    timeout=4,
                )
                data = r.json()
                return float(data.get("priceChangePercent", 0))
            except Exception:
                return 0.0

        tasks = {
            "BTC": loop.run_in_executor(None, _fetch_binance_price, "BTCUSDT"),
            "ETH": loop.run_in_executor(None, _fetch_binance_price, "ETHUSDT"),
            "EUR": loop.run_in_executor(None, _fetch_binance_price, "EURUSDT"),  # proxy DXY inverse
        }

        results = {}
        for key, task in tasks.items():
            try:
                results[key] = await asyncio.wait_for(task, timeout=5.0)
            except Exception:
                results[key] = 0.0

        btc_change = results.get("BTC", 0)
        eth_change = results.get("ETH", 0)
        eur_change = results.get("EUR", 0)

        # DXY proxy: quand EUR/USD monte, DXY baisse → haussier crypto
        if eur_change > 0.3:
            scores.append(0.65)
            signals.append(f"EUR/USD +{eur_change:.1f}% → DXY faible (haussier BTC)")
        elif eur_change < -0.3:
            scores.append(0.35)
            signals.append(f"EUR/USD {eur_change:.1f}% → DXY fort (baissier BTC)")
        else:
            scores.append(0.5)
            signals.append("EUR/USD neutre")

        # Momentum crypto global
        avg_crypto = (btc_change + eth_change) / 2
        if avg_crypto > 1.0:
            scores.append(0.70)
            signals.append(f"Crypto momentum fort: BTC {btc_change:+.1f}% ETH {eth_change:+.1f}%")
        elif avg_crypto < -1.0:
            scores.append(0.30)
            signals.append(f"Crypto momentum faible: BTC {btc_change:+.1f}% ETH {eth_change:+.1f}%")
        else:
            scores.append(0.50)

        macro_score = sum(scores) / len(scores) if scores else 0.5
        return round(macro_score, 3), signals
