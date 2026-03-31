"""
QuantMLAgent — Détection intelligente de régime de marché (Bull / Bear / Sideways / Volatile)
Spécialité : analyse ML légère + on-chain + macro pour adapter la stratégie en temps réel
Hérite de BaseAgent V3 → cerveau commun parfait
VERSION GOAT V9 — Wall Street + AI Engineer + FIX import time + vraies API
"""

from agents.base_agent import BaseAgent
from typing import Dict, Any
import asyncio
import requests
import time  # FIX CRITIQUE : import manquant causait NameError
import numpy as np
from datetime import datetime
from logging_config import logger


class QuantMLAgent(BaseAgent):
    """AGENT SPÉCIALISÉ QUANT ML — Détecte le régime de marché et adapte les paramètres."""

    def __init__(self):
        super().__init__(
            name="quant_ml",
            role=(
                "Détection de régime de marché (Bull/Bear/Sideways/Volatile) avec ML léger + "
                "on-chain + macro — adaptation automatique de la stratégie"
            )
        )
        self.regime = "NEUTRAL"
        self.confidence = 0.0
        self.last_regime_ts = 0
        self.regime_history = []
        self._fg_cache = {"value": 50, "ts": 0}       # Fear & Greed cache
        self._macro_cache = {"value": "NEUTRAL", "ts": 0}  # Macro trend cache

    # ────────────────────────────────────────────────────────────────────────
    # DONNÉES RÉELLES
    # ────────────────────────────────────────────────────────────────────────

    def _fetch_fear_greed(self) -> int:
        """Récupère le Fear & Greed Index en temps réel (Alternative.me)."""
        now = time.time()
        if now - self._fg_cache["ts"] < 300:  # cache 5 min
            return self._fg_cache["value"]
        try:
            r = requests.get(
                "https://api.alternative.me/fng/?limit=1&format=json",
                timeout=8
            )
            if r.status_code == 200:
                val = int(r.json()["data"][0]["value"])
                self._fg_cache = {"value": val, "ts": now}
                return val
        except Exception as e:
            logger.warning(f"[QuantML] Fear&Greed fetch error: {e}")
        return self._fg_cache["value"]

    def _fetch_btc_dominance(self) -> float:
        """Récupère la dominance BTC via CoinGecko (indicateur macro)."""
        try:
            r = requests.get(
                "https://api.coingecko.com/api/v3/global",
                timeout=8
            )
            if r.status_code == 200:
                dom = r.json().get("data", {}).get("market_cap_percentage", {}).get("btc", 50.0)
                return float(dom)
        except Exception as e:
            logger.warning(f"[QuantML] BTC dominance fetch error: {e}")
        return 50.0

    def _fetch_btc_24h_change(self) -> float:
        """Récupère la variation 24h du BTC pour jauger le momentum macro."""
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/24hr",
                params={"symbol": "BTCUSDT"},
                timeout=8
            )
            if r.status_code == 200:
                return float(r.json().get("priceChangePercent", 0))
        except Exception as e:
            logger.warning(f"[QuantML] BTC 24h change error: {e}")
        return 0.0

    # ────────────────────────────────────────────────────────────────────────
    # ML LÉGER — DÉTECTION RÉGIME
    # ────────────────────────────────────────────────────────────────────────

    def _compute_ml_regime(self, context: dict) -> Dict[str, Any]:
        """
        ML léger ultra-rapide — combine plusieurs sources de données réelles.
        Priorité aux données réelles > données du contexte > valeurs par défaut.
        """
        try:
            # Données en temps réel
            fg_live = self._fetch_fear_greed()
            btc_dom = self._fetch_btc_dominance()
            btc_chg = self._fetch_btc_24h_change()

            # Données du contexte (enrichies par les autres agents)
            fg = context.get("fg_value", fg_live)
            macro = context.get("macro_trend", "NEUTRAL")
            rsi = context.get("rsi", 50)
            vol = context.get("volatility", abs(btc_chg) / 3.0)
            mcap_chg = context.get("mcap_change_24h", btc_chg)

            # Features normalisées [-1 ; +1]
            fg_score      = (fg - 50) / 50.0
            macro_score   = 1.0 if macro == "BULL" else -1.0 if macro == "BEAR" else 0.0
            rsi_score     = (rsi - 50) / 50.0
            vol_score     = min(abs(vol) / 5.0, 2.0)
            onchain_score = mcap_chg / 10.0                 # btc_chg normalisé
            dom_score     = (btc_dom - 50) / 50.0           # > 50 = risk-off (bearish alt)

            # Score composite pondéré
            ml_score = (
                0.30 * fg_score +
                0.20 * macro_score +
                0.18 * rsi_score +
                0.15 * onchain_score -
                0.10 * vol_score +
                0.07 * dom_score
            )
            ml_score = max(-1.0, min(1.0, ml_score))

            # Détection régime
            if ml_score > 0.50:
                regime, conf = "BULL",      0.92
            elif ml_score < -0.50:
                regime, conf = "BEAR",      0.90
            elif abs(ml_score) < 0.20 and vol_score < 0.8:
                regime, conf = "SIDEWAYS",  0.85
            else:
                regime, conf = "VOLATILE",  0.88

            # Smoothing sur 5 dernières mesures
            self.regime_history.append(regime)
            if len(self.regime_history) > 5:
                self.regime_history = self.regime_history[-5:]
            final_regime = max(set(self.regime_history), key=self.regime_history.count)

            return {
                "regime":     final_regime,
                "ml_score":   round(ml_score, 3),
                "confidence": conf,
                "reason":     (
                    f"FG:{fg} | BTC 24h:{btc_chg:+.1f}% | Dom:{btc_dom:.0f}% | "
                    f"RSI:{rsi} | Vol:{vol:.1f}x | Macro:{macro}"
                ),
                "fg_live": fg_live,
                "btc_change_24h": btc_chg,
                "btc_dominance":  btc_dom,
            }
        except Exception as e:
            logger.warning(f"[QuantML] Erreur calcul régime: {e}")
            return {"regime": "NEUTRAL", "ml_score": 0.0, "confidence": 0.5, "reason": "fallback"}

    # ────────────────────────────────────────────────────────────────────────
    # DOMAINE & RÉPONSE
    # ────────────────────────────────────────────────────────────────────────

    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        keywords = [
            "regime", "market regime", "bull", "bear", "sideways", "volatile",
            "trend", "macro", "ml", "quant", "fear", "greed", "dominance",
            # participation au débat collectif
            "synthèse", "débat", "cerveau collectif", "final decision",
            "raffine", "trade ou no trade", "micro", "analyse collective",
        ]
        return any(kw in q for kw in keywords)

    def explain_term(self, term: str) -> str:
        glossary = {
            "regime":    "État actuel du marché (Bull=haussier, Bear=baissier, Sideways=range, Volatile=fort mouvement)",
            "bull":      "Marché haussier — stratégie agressive + plus de taille",
            "bear":      "Marché baissier — réduction taille + hedging actif",
            "sideways":  "Marché sans tendance claire — micro-trading + yield staking prioritaire",
            "volatile":  "Marché très agité — réduction risque + trailing serré",
            "ml_score":  "Score ML combinant Fear&Greed, RSI, MACD, volume, BTC dominance et données on-chain",
            "winrate":   "Taux de réussite des trades — objectif 95%+",
        }
        return glossary.get(term.lower(), term)

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Réponse ultra-spécialisée + cerveau commun + données réelles."""
        if not self._is_in_my_domain(question):
            return {
                "agent":          self.name,
                "summary":        "⚠️ Je suis spécialisé UNIQUEMENT en détection de régime de marché.",
                "confidence":     0.0,
                "recommendation": "Demande à TraderAgent ou RiskAgent",
                "warning":        "Hors domaine quant_ml",
            }

        shared_glossary = context.get("shared_glossary", {})
        def explain(k):
            return self.explain_term(k) or shared_glossary.get(k, k)

        result = self._compute_ml_regime(context)

        # Mise à jour état interne
        self.regime           = result["regime"]
        self.confidence       = result["confidence"]
        self.last_regime_ts   = time.time()

        # Recommandations opérationnelles pour les autres agents
        recommendations = {
            "BULL":     "Augmente taille positions + priorise momentum + désactive hedging + cibles TP élargies",
            "BEAR":     "Réduit taille 50% + active hedging + priorise yield staking + SL serrés",
            "SIDEWAYS": "Mode micro-trading + staking prioritaire + trailing serré + cibles TP courtes",
            "VOLATILE": "Mode ultra-conservateur : réduction risque 70% + trailing très serré + micro uniquement",
        }
        recommendation = recommendations.get(result["regime"], "Surveiller avant de trader")

        full_summary = (
            f"📊 Analyse QuantML complète — {explain('regime')} actuel = {result['regime']} "
            f"(score ML: {result['ml_score']:+.3f} | conf: {result['confidence']:.0%}) | "
            f"Fear&Greed live: {result.get('fg_live', '?')} | "
            f"BTC 24h: {result.get('btc_change_24h', 0):+.1f}% | "
            f"BTC Dominance: {result.get('btc_dominance', 0):.0f}% | "
            f"Raison: {result['reason']}"
        )

        return {
            "agent":          self.name,
            "summary":        f"📊 Régime détecté : {result['regime']} (conf {result['confidence']:.0%})",
            "regime":         result["regime"],
            "ml_score":       result["ml_score"],
            "confidence":     result["confidence"],
            "reason":         result["reason"],
            "recommendation": recommendation,
            "fg_live":        result.get("fg_live", 50),
            "btc_change_24h": result.get("btc_change_24h", 0),
            "btc_dominance":  result.get("btc_dominance", 50),
            "glossary_used":  True,
            "full_summary":   full_summary,
        }
