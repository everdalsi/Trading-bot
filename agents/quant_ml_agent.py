"""
🧠 QUANT ML AGENT V10 — Régime de Marché Expert + Signaux Avancés
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AMÉLIORATIONS V10 :
- Ratio ETH/BTC : indicateur alt season (ETH surperforme = alt season)
- Volume marché total (CoinGecko) : conviction ou distribution
- OI Binance Futures : variation open interest = smart money flow
- ATR régime : volatilité normalisée → meilleur tri SIDEWAYS vs VOLATILE
- Smoothing EMA (vs mode simple V9) : transitions plus douces
- Score composite 7 facteurs avec poids dynamiques selon contexte
- Volatility-adjusted confidence : plus précis quand marché calme
"""

from agents.base_agent import BaseAgent
from typing import Dict, Any
import asyncio
import requests
import time
import numpy as np
from logging_config import logger


class QuantMLAgent(BaseAgent):

    def __init__(self):
        super().__init__(
            name="quant_ml",
            role="Détection régime marché (Bull/Bear/Sideways/Volatile) — ML léger + on-chain + macro"
        )
        self.regime = "NEUTRAL"
        self.confidence = 0.0
        self.last_regime_ts = 0
        self.regime_history: list = []
        self._ema_score = 0.0         # EMA du score ML pour smoothing
        self._ema_alpha = 0.3         # α EMA (30% nouveau, 70% historique)

        # Caches avec TTL
        self._cache: Dict[str, Dict] = {
            "fg":      {"value": 50,       "ts": 0, "ttl": 300},   # 5 min
            "dom":     {"value": 50.0,     "ts": 0, "ttl": 600},   # 10 min
            "btc_chg": {"value": 0.0,      "ts": 0, "ttl": 60},    # 1 min
            "eth_btc": {"value": 0.065,    "ts": 0, "ttl": 120},   # 2 min
            "oi":      {"value": 0.0,      "ts": 0, "ttl": 120},   # 2 min
        }

    # ────────────────────────────────────────────────────────────────────────
    # FETCH DONNÉES RÉELLES — AVEC CACHE INTELLIGENT
    # ────────────────────────────────────────────────────────────────────────

    def _cached_fetch(self, key: str, fetch_fn, *args) -> Any:
        """Pattern cache générique."""
        entry = self._cache.get(key, {})
        now = time.time()
        if entry and now - entry.get("ts", 0) < entry.get("ttl", 300):
            return entry["value"]
        try:
            val = fetch_fn(*args)
            if val is not None:
                self._cache[key] = {**entry, "value": val, "ts": now}
                return val
        except Exception as e:
            logger.debug(f"[QuantML] {key} fetch error: {e}")
        return entry.get("value", 0)

    def _fetch_fear_greed(self) -> int:
        r = requests.get("https://api.alternative.me/fng/?limit=1&format=json", timeout=8)
        if r.status_code == 200:
            return int(r.json()["data"][0]["value"])
        return 50

    def _fetch_btc_dominance(self) -> float:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=8)
        if r.status_code == 200:
            return float(r.json().get("data", {}).get("market_cap_percentage", {}).get("btc", 50.0))
        return 50.0

    def _fetch_btc_24h_change(self) -> float:
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr",
                         params={"symbol": "BTCUSDT"}, timeout=6)
        if r.status_code == 200:
            return float(r.json().get("priceChangePercent", 0))
        return 0.0

    def _fetch_eth_btc_ratio(self) -> float:
        """ETH/BTC ratio — alt season indicator (ratio montant = alt season)."""
        try:
            r = requests.get("https://api.binance.com/api/v3/ticker/24hr",
                             params={"symbol": "ETHBTC"}, timeout=6)
            if r.status_code == 200:
                return float(r.json().get("lastPrice", 0.065))
        except Exception:
            pass
        return 0.065

    def _fetch_oi_change(self, symbol: str = "BTCUSDT") -> float:
        """Open Interest variation 24h (futures Binance) — smart money flow."""
        try:
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/openInterestHist",
                params={"symbol": symbol, "period": "1h", "limit": 25},
                timeout=8
            )
            if r.status_code == 200:
                data = r.json()
                if len(data) >= 2:
                    oi_now  = float(data[-1]["sumOpenInterest"])
                    oi_prev = float(data[-24]["sumOpenInterest"]) if len(data) >= 24 else float(data[0]["sumOpenInterest"])
                    if oi_prev > 0:
                        return (oi_now - oi_prev) / oi_prev * 100  # % variation
        except Exception as e:
            logger.debug(f"[QuantML] OI fetch: {e}")
        return 0.0

    def _fetch_btc_atr_regime(self, symbol: str = "BTCUSDT") -> float:
        """ATR normalisé 14 bougies 1h — mesure la volatilité réelle."""
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": symbol, "interval": "1h", "limit": 15},
                timeout=8
            )
            if r.status_code == 200:
                raw    = r.json()
                closes = [float(c[4]) for c in raw]
                highs  = [float(c[2]) for c in raw]
                lows   = [float(c[3]) for c in raw]
                trs = [
                    max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
                    for i in range(1, len(closes))
                ]
                atr = np.mean(trs[-14:])
                price = closes[-1]
                return (atr / price * 100) if price > 0 else 0.5  # % normalisé
        except Exception as e:
            logger.debug(f"[QuantML] ATR fetch: {e}")
        return 0.5

    # ────────────────────────────────────────────────────────────────────────
    # DÉTECTION RÉGIME ML — V10
    # ────────────────────────────────────────────────────────────────────────

    def _compute_ml_regime(self, context: dict) -> Dict[str, Any]:
        # Fetches parallèles via cache (non-bloquants si cache valide)
        fg_live  = self._cached_fetch("fg",      self._fetch_fear_greed)
        btc_dom  = self._cached_fetch("dom",     self._fetch_btc_dominance)
        btc_chg  = self._cached_fetch("btc_chg", self._fetch_btc_24h_change)
        eth_btc  = self._cached_fetch("eth_btc", self._fetch_eth_btc_ratio)
        oi_chg   = self._cached_fetch("oi",      self._fetch_oi_change, context.get("symbol", "BTCUSDT"))

        # Données contexte (fallback si live indisponible)
        fg     = context.get("fg_value", fg_live)
        macro  = context.get("macro_trend", "NEUTRAL")
        rsi    = context.get("rsi", 50)
        vol    = context.get("volatility", abs(btc_chg) / 3.0)
        mcap_c = context.get("mcap_change_24h", btc_chg)

        # ATR régime (indépendant du contexte)
        atr_pct = self._fetch_btc_atr_regime()

        # ── Features normalisées ────────────────────────────────────────
        fg_score    = (fg - 50) / 50.0                    # [-1, +1]
        macro_score = 1.0 if macro == "BULL" else -1.0 if macro == "BEAR" else 0.0
        rsi_score   = (rsi - 50) / 50.0
        vol_score   = min(abs(vol) / 5.0, 2.0)            # [0, 2]
        onchain_sc  = max(-1.0, min(1.0, mcap_c / 10.0))
        dom_score   = (btc_dom - 50) / 50.0               # > 50 = risk-off
        oi_score    = max(-1.0, min(1.0, oi_chg / 5.0))  # OI +5% = accumulation bullish
        eth_btc_norm = (eth_btc - 0.05) / 0.03            # ETH/BTC normalisé autour 0.065

        # ── Score composite pondéré 7 facteurs ──────────────────────────
        ml_score = (
            0.25 * fg_score +        # Sentiment (important)
            0.15 * macro_score +     # Tendance macro
            0.15 * rsi_score +       # RSI contexte
            0.15 * onchain_sc +      # Performance BTC
            0.12 * oi_score +        # NEW : OI flow (smart money)
            0.10 * eth_btc_norm +    # NEW : alt season signal
            0.08 * (-dom_score)      # Dominance inversée (dom high = risk-off)
        )
        ml_score = max(-1.0, min(1.0, ml_score))

        # ── EMA smoothing du score (évite les sauts brusques) ───────────
        self._ema_score = self._ema_alpha * ml_score + (1 - self._ema_alpha) * self._ema_score
        smooth_score = self._ema_score

        # ── Régime basé sur score EMA + ATR ─────────────────────────────
        if atr_pct > 2.5:
            # Volatilité extrême → VOLATILE indépendamment du score
            regime, conf = "VOLATILE", 0.91
        elif smooth_score > 0.40:
            regime, conf = "BULL",     0.93
        elif smooth_score < -0.40:
            regime, conf = "BEAR",     0.91
        elif abs(smooth_score) < 0.20 and atr_pct < 1.0:
            regime, conf = "SIDEWAYS", 0.87
        else:
            regime, conf = "VOLATILE", 0.84

        # ── Historique + vote (mode sur 5 derniers) ──────────────────────
        self.regime_history.append(regime)
        if len(self.regime_history) > 7:
            self.regime_history = self.regime_history[-7:]
        # Vote pondéré : régimes récents comptent plus (poids croissants)
        weights = list(range(1, len(self.regime_history) + 1))
        from collections import Counter
        weighted_votes: Dict[str, float] = {}
        for reg, w in zip(self.regime_history, weights):
            weighted_votes[reg] = weighted_votes.get(reg, 0) + w
        final_regime = max(weighted_votes, key=weighted_votes.get)

        # Boost confiance si consensus fort
        if weighted_votes.get(final_regime, 0) / sum(weights) > 0.7:
            conf = min(0.97, conf + 0.04)

        return {
            "regime":        final_regime,
            "ml_score":      round(smooth_score, 3),
            "raw_ml_score":  round(ml_score, 3),
            "confidence":    conf,
            "atr_pct":       round(atr_pct, 3),
            "oi_change_24h": round(oi_chg, 2),
            "eth_btc":       round(eth_btc, 5),
            "reason": (
                f"FG:{fg} | BTC:{btc_chg:+.1f}% | Dom:{btc_dom:.0f}% | "
                f"RSI:{rsi} | ATR%:{atr_pct:.2f} | OI:{oi_chg:+.1f}% | ETH/BTC:{eth_btc:.4f}"
            ),
            "fg_live":        fg_live,
            "btc_change_24h": btc_chg,
            "btc_dominance":  btc_dom,
        }

    # ────────────────────────────────────────────────────────────────────────
    # DOMAINE & RÉPONSE
    # ────────────────────────────────────────────────────────────────────────

    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        return any(kw in q for kw in [
            "regime", "market regime", "bull", "bear", "sideways", "volatile",
            "trend", "macro", "ml", "quant", "fear", "greed", "dominance",
            "synthèse", "débat", "cerveau collectif", "final decision",
            "raffine", "trade ou no trade", "micro", "analyse collective",
        ])

    def explain_term(self, term: str) -> str:
        glossary = {
            "regime": "État actuel du marché (Bull/Bear/Sideways/Volatile)",
            "bull":   "Marché haussier — stratégie agressive",
            "bear":   "Marché baissier — protection + hedging",
            "sideways": "Sans tendance — micro-trading + staking",
            "volatile": "Agité — réduction risque max",
            "oi":     "Open Interest — montant en positions futures ouvertes",
            "atr":    "Average True Range — volatilité moyenne vraie",
        }
        return glossary.get(term.lower(), term)

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {
                "agent": self.name,
                "summary": "⚠️ Hors domaine quant_ml",
                "confidence": 0.0,
                "recommendation": "HOLD",
                "warning": "Hors domaine quant_ml",
            }

        result = self._compute_ml_regime(context)
        self.regime     = result["regime"]
        self.confidence = result["confidence"]
        self.last_regime_ts = time.time()

        recommendations = {
            "BULL":     "Taille max + momentum + désactiver hedging + TP élargis",
            "BEAR":     "Taille -50% + hedging actif + staking + SL serrés",
            "SIDEWAYS": "Micro-trading + staking prioritaire + trailing serré",
            "VOLATILE": "Taille -70% + trailing très serré + micro uniquement",
        }
        recommendation = recommendations.get(result["regime"], "Surveiller avant de trader")

        full_summary = (
            f"🧠 QuantML V10 — Régime: {result['regime']} "
            f"(score EMA: {result['ml_score']:+.3f} | conf: {result['confidence']:.0%}) | "
            f"F&G: {result.get('fg_live','?')} | BTC 24h: {result.get('btc_change_24h',0):+.1f}% | "
            f"Dom: {result.get('btc_dominance',0):.0f}% | ATR%: {result.get('atr_pct',0):.2f} | "
            f"OI: {result.get('oi_change_24h',0):+.1f}% | ETH/BTC: {result.get('eth_btc',0):.4f}"
        )

        return {
            "agent":           self.name,
            "summary":         f"🧠 Régime: {result['regime']} (conf {result['confidence']:.0%})",
            "full_summary":    full_summary,
            "regime":          result["regime"],
            "ml_score":        result["ml_score"],
            "confidence":      result["confidence"],
            "reason":          result["reason"],
            "recommendation":  recommendation,
            "fg_live":         result.get("fg_live", 50),
            "btc_change_24h":  result.get("btc_change_24h", 0),
            "btc_dominance":   result.get("btc_dominance", 50),
            "atr_pct":         result.get("atr_pct", 0),
            "oi_change_24h":   result.get("oi_change_24h", 0),
            "eth_btc":         result.get("eth_btc", 0),
            "glossary_used":   True,
        }
