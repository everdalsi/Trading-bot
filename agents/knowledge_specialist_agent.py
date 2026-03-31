"""
📚 KNOWLEDGE SPECIALIST AGENT V3 — Expert théorique + FIX singleton KB + Cerveau commun
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIXES V3 :
- Utilise _KnowledgeBaseSingleton au lieu de créer une nouvelle instance KB (bug majeur)
- Glossaire partagé automatique
- RAG cross-theory croisée avec historique trades réels
- Réponses plus expertes (Wyckoff, VSA, Smart Money, CFA level)
"""

from agents.base_agent import BaseAgent, _KnowledgeBaseSingleton
from typing import Dict, Any, List
from logging_config import logger


# Termes théoriques que l'agent maîtrise
WYCKOFF_TERMS = [
    "accumulation", "distribution", "spring", "upthrust", "shakeout",
    "composite man", "test", "sign of strength", "sign of weakness",
    "backup to edge of creek", "last point of support",
]
VSA_TERMS = [
    "supply", "demand", "effort vs result", "no demand", "stopping volume",
    "ultra high volume", "narrow spread", "wide spread", "professional money",
]
SMART_MONEY_TERMS = [
    "order block", "fair value gap", "fvg", "imbalance", "liquidity",
    "equal highs", "equal lows", "breaker block", "mitigation", "premium",
    "discount", "bos", "break of structure", "choch", "change of character",
    "displacement", "sweep", "inducement",
]


class KnowledgeSpecialistAgent(BaseAgent):

    def __init__(self):
        super().__init__(
            name="knowledge_specialist",
            role=(
                "Expert théorique élite : Wyckoff, VSA, Smart Money (ICT/SMC), CFA, "
                "Kelly Criterion — croise la théorie avec l'historique réel des trades"
            )
        )
        # FIX CRITIQUE : utilise le singleton au lieu de créer une nouvelle instance
        self.kb = _KnowledgeBaseSingleton.get_instance()

    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        keywords = [
            "wyckoff", "vsa", "smart money", "smc", "ict", "order block",
            "fvg", "liquidity", "theory", "théorie", "knowledge", "connaissance",
            "pattern", "setup", "accumulation", "distribution", "spring",
            "cfa", "kelly", "risk management", "stratégie", "strategy",
            # débat collectif
            "synthèse", "débat", "cerveau collectif", "final decision", "raffine",
            "trade ou no trade", "micro", "analyse collective",
        ]
        return any(kw in q for kw in keywords)

    def _detect_wyckoff_phase(self, context: dict) -> Dict[str, Any]:
        """Détecte la phase Wyckoff selon les données de marché."""
        rsi = context.get("rsi", 50)
        volume = context.get("volume_data", {})
        change_pct = context.get("change_pct", 0)
        market_data = context.get("market_data", {})
        trend = market_data.get("trend", "neutre")

        volume_spike = volume.get("volume_spike", False) if isinstance(volume, dict) else False

        # Logique Wyckoff simplifiée
        if rsi < 35 and volume_spike and change_pct < -3:
            return {
                "phase":       "Selling Climax / Spring possible",
                "bias":        "BULLISH",
                "action":      "Surveiller le test du low avec volume décroissant",
                "confidence":  0.82,
            }
        elif rsi > 68 and volume_spike and change_pct > 3:
            return {
                "phase":       "Buying Climax / Upthrust possible",
                "bias":        "BEARISH",
                "action":      "Prudence — distribution possible imminente",
                "confidence":  0.80,
            }
        elif 45 <= rsi <= 55 and not volume_spike:
            return {
                "phase":       "Accumulation / Consolidation",
                "bias":        "NEUTRAL",
                "action":      "Attendre le break du range avec volume confirmant",
                "confidence":  0.70,
            }
        else:
            return {
                "phase":       "Phase non identifiée",
                "bias":        "NEUTRAL",
                "action":      "Analyser sur timeframe supérieur (4H/Daily)",
                "confidence":  0.55,
            }

    def _detect_smc_setup(self, context: dict) -> Dict[str, Any]:
        """Détecte les setups Smart Money Concepts (ICT)."""
        rsi = context.get("rsi", 50)
        funding = context.get("funding_rate", 0)
        change_pct = context.get("change_pct", 0)

        if rsi < 40 and funding < -0.001:
            return {
                "setup":      "Discount zone + Funding négatif → Long Opportunity",
                "bias":       "BULLISH",
                "target":     "Fair Value Gap supérieur / Equal Highs",
                "confidence": 0.80,
            }
        elif rsi > 65 and funding > 0.001:
            return {
                "setup":      "Premium zone + Funding positif → Short Opportunity",
                "bias":       "BEARISH",
                "target":     "Fair Value Gap inférieur / Equal Lows",
                "confidence": 0.78,
            }
        else:
            return {
                "setup":      "Pas de setup SMC clair identifié",
                "bias":       "NEUTRAL",
                "target":     "Surveiller la liquidité des equal highs/lows",
                "confidence": 0.50,
            }

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {
                "agent":          self.name,
                "summary":        "⚠️ Knowledge Specialist hors spécialité → ignoré",
                "confidence":     0.0,
                "recommendation": "HOLD - Hors domaine knowledge_specialist",
                "warning":        "Hors domaine knowledge_specialist",
            }

        shared_glossary = context.get("shared_glossary", {})
        def explain(k):
            return self.explain_term(k) or shared_glossary.get(k, k)

        symbol     = context.get("symbol", "UNKNOWN")
        market_data = context.get("market_data", {})
        price      = market_data.get("price", context.get("price", "N/A"))
        rsi        = context.get("rsi", market_data.get("rsi", 50))
        trend      = market_data.get("trend", "neutre")

        # Construction query RAG intelligente
        rag_query = (
            f"Situation : {symbol} RSI={rsi} tendance={trend} prix={price} — {question}\n"
            f"Analyse selon Wyckoff, VSA, Smart Money Concepts. Trouve patterns historiques."
        )
        theoretical_context = self.kb.get_context_for_agent(rag_query, max_results=5)

        # Analyses théoriques
        wyckoff = self._detect_wyckoff_phase(context)
        smc     = self._detect_smc_setup(context)

        # Croisement avec leçons réelles
        best_patterns = context.get("best_patterns", [])
        insights      = context.get("insights", [])
        lesson_count  = context.get("lesson_count", 0)

        # Score de confiance théorique
        theory_conf = (wyckoff["confidence"] + smc["confidence"]) / 2
        if lesson_count > 50:
            theory_conf = min(0.95, theory_conf + 0.08)

        # Recommandation unifiée
        biases = [wyckoff["bias"], smc["bias"]]
        bull_count = biases.count("BULLISH")
        bear_count = biases.count("BEARISH")

        if bull_count > bear_count:
            recommendation = f"📗 BULLISH — {wyckoff['phase']} | {smc['setup']}"
        elif bear_count > bull_count:
            recommendation = f"📕 BEARISH — {wyckoff['phase']} | {smc['setup']}"
        else:
            recommendation = f"⚖️ NEUTRE — {wyckoff['action']}"

        full_summary = (
            f"📚 Knowledge Specialist — {symbol} | "
            f"Wyckoff: {wyckoff['phase']} | SMC: {smc['setup']} | "
            f"Conf théorique: {theory_conf:.0%} | "
            f"Leçons croisées: {lesson_count} | "
            f"RAG: {'actif' if 'Aucune' not in theoretical_context else 'vide'}"
        )

        return {
            "agent":              self.name,
            "summary":            full_summary,
            "full_summary":       full_summary,
            "wyckoff":            wyckoff,
            "smc":                smc,
            "theoretical_context": theoretical_context[:500] + "..." if len(theoretical_context) > 500 else theoretical_context,
            "best_patterns":      best_patterns[:3],
            "lesson_count":       lesson_count,
            "arguments":          [
                f"Wyckoff Phase : {wyckoff['phase']}",
                f"SMC Setup : {smc['setup']}",
                f"Leçons croisées : {lesson_count}",
                f"RAG théorique actif : {'Oui' if 'Aucune' not in theoretical_context else 'Non'}",
            ],
            "risks":              (
                ["Pas de setup confirmé — attendre signal supplémentaire"]
                if wyckoff["bias"] == "NEUTRAL" and smc["bias"] == "NEUTRAL"
                else []
            ),
            "confidence":         theory_conf,
            "recommendation":     recommendation,
            "knowledge_loaded":   "Aucune" not in theoretical_context,
            "glossary_used":      True,
        }
