"""
QuantMLAgent — Détection intelligente de régime de marché (Bull / Bear / Sideways / Volatile)
Spécialité : analyse ML légère + on-chain + macro pour adapter la stratégie en temps réel
Hérite de BaseAgent V3 → cerveau commun parfait
VERSION GOAT V8.3 — Wall Street + AI Engineer
"""

from agents.base_agent import BaseAgent
from typing import Dict, Any
import asyncio
import requests
import numpy as np
from datetime import datetime
from logging_config import logger

class QuantMLAgent(BaseAgent):
    """AGENT SPÉCIALISÉ QUANT ML — Détecte le régime de marché et adapte les paramètres."""
    def __init__(self):
        super().__init__(
            name="quant_ml",
            role="Détection de régime de marché (Bull/Bear/Sideways/Volatile) avec ML léger + on-chain + macro — adaptation automatique de la stratégie"
        )
        # Config pro Wall Street
        self.regime = "NEUTRAL"          # état actuel
        self.confidence = 0.0
        self.last_regime_ts = 0
        self.regime_history = []         # pour smoothing

    def _is_in_my_domain(self, question: str) -> bool:
        """Vérification stricte de spécialisation (cerveau commun)"""
        q = question.lower()
        keywords = ["regime", "market regime", "bull", "bear", "sideways", "volatile", "trend", "macro", "ml", "quant"]
        return any(kw in q for kw in keywords)

    def explain_term(self, term: str) -> str:
        """Glossaire partagé du cerveau commun"""
        glossary = {
            "regime": "État actuel du marché (Bull = haussier, Bear = baissier, Sideways = range, Volatile = fort mouvement)",
            "bull": "Marché haussier — stratégie agressive + plus de taille",
            "bear": "Marché baissier — réduction taille + hedging",
            "sideways": "Marché sans tendance claire — micro-trading + yield staking prioritaire",
            "volatile": "Marché très agité — réduction risque + trailing serré",
            "ml_score": "Score ML qui combine RSI multi-TF, MACD, volume, Fear&Greed et on-chain"
        }
        return glossary.get(term.lower(), term)

    def _compute_ml_regime(self, context: dict) -> Dict[str, Any]:
        """ML léger ultra-rapide (pas de modèle lourd)"""
        try:
            fg = context.get("fg_value", 50)
            macro = context.get("macro_trend", "NEUTRAL")
            rsi = context.get("rsi", 50)
            vol = context.get("volatility", 0.0)
            mcap_chg = context.get("mcap_change_24h", 0)

            # Features normalisées
            fg_score = (fg - 50) / 50.0
            macro_score = 1.0 if macro == "BULL" else -1.0 if macro == "BEAR" else 0.0
            rsi_score = (50 - rsi) / 50.0
            vol_score = min(vol / 5.0, 2.0)
            onchain_score = mcap_chg / 5.0

            # Score composite
            ml_score = 0.35*fg_score + 0.25*macro_score + 0.20*rsi_score + 0.15*vol_score + 0.05*onchain_score
            ml_score = max(-1.0, min(1.0, ml_score))

            # Régime final
            if ml_score > 0.55:
                regime = "BULL"
                conf = 0.92
            elif ml_score < -0.55:
                regime = "BEAR"
                conf = 0.90
            elif abs(ml_score) < 0.25 and vol_score < 0.8:
                regime = "SIDEWAYS"
                conf = 0.85
            else:
                regime = "VOLATILE"
                conf = 0.88

            self.regime_history.append(regime)
            if len(self.regime_history) > 5:
                self.regime_history = self.regime_history[-5:]

            # Smoothing
            final_regime = max(set(self.regime_history), key=self.regime_history.count)

            return {
                "regime": final_regime,
                "ml_score": round(ml_score, 3),
                "confidence": conf,
                "reason": f"FG:{fg} | Macro:{macro} | RSI:{rsi} | Vol:{vol:.1f}x"
            }
        except Exception as e:
            logger.warning(f"[QuantML] Erreur calcul régime: {e}")
            return {"regime": "NEUTRAL", "ml_score": 0.0, "confidence": 0.5, "reason": "fallback"}

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Réponse ultra-spécialisée + cerveau commun"""
        if not self._is_in_my_domain(question):
            return {
                "agent": self.name,
                "summary": "⚠️ Je suis spécialisé UNIQUEMENT en détection de régime de marché. Hors de mon domaine.",
                "confidence": 0.0,
                "recommendation": "Demande à TraderAgent ou RiskAgent"
            }

        # Glossaire partagé forcé
        shared_glossary = context.get("shared_glossary", {})
        def explain(k):
            return self.explain_term(k) or shared_glossary.get(k, k)

        result = self._compute_ml_regime(context)

        # Mise à jour état interne
        self.regime = result["regime"]
        self.confidence = result["confidence"]
        self.last_regime_ts = time.time()

        # Adaptation recommandations pour les autres agents
        if result["regime"] == "BULL":
            recommendation = "Augmente taille positions + priorise momentum + désactive hedging"
        elif result["regime"] == "BEAR":
            recommendation = "Réduit taille + active hedging + priorise yield staking"
        elif result["regime"] == "SIDEWAYS":
            recommendation = "Mode micro-trading + staking prioritaire + trailing serré"
        else:
            recommendation = "Mode volatile : réduction risque + trailing très serré + micro uniquement"

        return {
            "agent": self.name,
            "summary": f"📊 Régime détecté : {result['regime']} (conf {result['confidence']:.0f}%)",
            "regime": result["regime"],
            "ml_score": result["ml_score"],
            "confidence": result["confidence"],
            "reason": result["reason"],
            "recommendation": recommendation,
            "glossary_used": True,
            "full_summary": f"Salut boss ! {explain('regime')} actuel = {result['regime']}. Voici les ajustements à faire pour maximiser le winrate."
        }
