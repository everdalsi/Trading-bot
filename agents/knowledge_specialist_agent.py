"""
📚 KNOWLEDGE SPECIALIST AGENT — Le "Professeur" du bot
Version 1.0 — Intégration RAG pro + croisement avec trades réels
"""

from agents.base_agent import BaseAgent
from knowledge_base import KnowledgeBase
from typing import Dict, Any
from logging_config import logger

class KnowledgeSpecialistAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="knowledge_specialist",
            role="Expert théorique : Wyckoff, VSA, CFA, Smart Money — croise PDFs avec historique trades"
        )
        self.kb = KnowledgeBase()  # réutilise ta KnowledgeBase existante

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        symbol = context.get("symbol", "UNKNOWN")
        market_data = context.get("market_data", {})
        price = market_data.get("price", "N/A")
        setup = f"{market_data.get('trend', 'neutre')} | RSI {market_data.get('rsi', 'N/A')} | Volume spike ?"

        # 1. Construction d'une query intelligente pour le RAG
        query = f"""
        Situation actuelle : {symbol} {setup} — Prix {price}
        Question du trade : {question}
        Analyse selon Wyckoff / VSA / CFA / accumulation-distribution.
        Cherche les patterns historiques qui matchent.
        """

        # 2. Récupération du contexte théorique (RAG)
        theoretical_context = self.kb.get_context_for_agent(query, max_results=6)

        # 3. Croisement avec les leçons réelles (LearningAgent)
        best_patterns = context.get("best_patterns", [])
        insights = context.get("insights", [])
        lesson_count = context.get("lesson_count", 0)

        # 4. Analyse finale (logique pure + structurée comme les autres agents)
        confidence = 0.85 if lesson_count > 30 else 0.65

        if "accumulation" in theoretical_context.lower() or "spring" in theoretical_context.lower():
            recommendation = "✅ Setup Wyckoff/VSA classique détecté → forte probabilité d'inversion haussière"
            risks = ["Vérifier le volume confirmant le spring"]
        elif "upthrust" in theoretical_context.lower():
            recommendation = "⚠️ Upthrust détecté → possible distribution → prudence"
            risks = ["Risque de fakeout élevé"]
        else:
            recommendation = "📖 Connaissances théoriques disponibles mais pas de pattern clair identifié"
            risks = ["Besoin de plus de contexte price action"]

        # 5. On génère une nouvelle leçon potentielle pour le LearningAgent
        new_lesson = {
            "pattern": f"Knowledge match : {symbol} {setup}",
            "lecon": f"{theoretical_context[:400]}... (source PDFs)",
            "confidence": confidence,
            "tags": ["wyckoff", "vsa", "theory"]
        }

        logger.info(f"📚 [KNOWLEDGE SPECIALIST] Query sur {symbol} → {len(theoretical_context)} chars de théorie")

        return {
            "agent": self.name,
            "summary": f"Analyse théorique {symbol} : {recommendation[:80]}",
            "arguments": [
                f"Contexte RAG : {len(theoretical_context)} caractères des PDFs",
                f"{lesson_count} leçons croisées",
                f"Meilleurs patterns Learning : {len(best_patterns)}",
                f"Query utilisée : {query[:120]}..."
            ],
            "risks": risks,
            "confidence": confidence,
            "recommendation": recommendation,
            "new_lesson_for_learning": new_lesson,   # ← on le passera au LearningAgent plus tard
            "theoretical_context": theoretical_context[:800]  # pour debug / orchestrator
        }
