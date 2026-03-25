"""
🚀 EVOLUTION AGENT V1 — Auto-évolution profonde utilisant l’Orchestrator existant
Utilise directement tout le système multi-agents pour décider des améliorations.
"""

from agents.base_agent import BaseAgent
from typing import Dict, Any
import asyncio
from .tools import EditBotFileTool, GitPushTool   # tes tools déjà créés
from crewai_tools import CodeInterpreterTool

class EvolutionAgent(BaseAgent):

    def __init__(self, orchestrator):
        super().__init__(
            name="evolution",
            role="Agent d'évolution autonome — analyse + code + déploiement"
        )
        self.orchestrator = orchestrator          # ← accès direct à TON Orchestrator
        self.edit_tool = EditBotFileTool()
        self.push_tool = GitPushTool()
        self.code_tool = CodeInterpreterTool()

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        print("🧬 [EVOLUTION] Lancement d'un cycle d'auto-évolution...")

        # === 1. Analyse complète via ton Orchestrator existant ===
        memory = context.get("memory", {})
        # On simule un "market_data" léger pour lancer le pipeline complet
        dummy_market = {"symbol": "BTCUSDT", "price": 65000, "timestamp": "now"}

        try:
            # On utilise directement ton orchestrator.run() pour avoir les vraies stats
            analysis_result = await self.orchestrator.run(dummy_market, memory)
            stats = self.orchestrator.performance.get_global_stats(memory)
            lesson_count = self.orchestrator.learning.get_lesson_count()
        except Exception as e:
            return {"agent": "evolution", "summary": f"Erreur orchestrator: {e}", "decision": "HOLD"}

        # === 2. Diagnostic intelligent ===
        wr = stats.get("winrate", 0)
        sharpe = stats.get("sharpe", 0)
        streak = stats.get("streak_count", 0)
        degraded = stats.get("degraded", False)
        drawdown = context.get("drawdown", 0)

        needs_improvement = (
            wr < 52 or
            sharpe < 0.8 or
            streak >= 6 or
            degraded or
            drawdown <= -0.08 or
            lesson_count < 500
        )

        if not needs_improvement:
            return {
                "agent": "evolution",
                "summary": "✅ Système déjà optimal — pas d’évolution nécessaire",
                "decision": "NO CHANGE",
                "confidence": 0.95
            }

        # === 3. Proposition d’amélioration + génération de code ===
        prompt = f"""
        Objectif principal : {context.get('main_objective', 'Améliorer constamment le bot')}
        
        Stats actuelles :
        - Winrate : {wr}%
        - Sharpe : {sharpe}
        - Streak : {streak}
        - Dégradé : {degraded}
        - Leçons en DB : {lesson_count}
        
        Propose UNE amélioration concrète et sûre (nouveau paramètre, règle auto, ajustement RiskAgent, etc.).
        Retourne UNIQUEMENT du code Python valide prêt à être écrit dans bot.py ou un agent.
        """

        code_suggestion = await self.code_tool._run(prompt)   # utilise le CodeInterpreter

        # === 4. Application du changement ===
        edit_result = self.edit_tool._run(new_code=code_suggestion, filename="bot.py")

        # === 5. Push automatique ===
        commit_msg = f"🧬 EvolutionAgent — Amélioration auto (WR:{wr} → optimisé)"
        push_result = self.push_tool._run(commit_message=commit_msg)

        return {
            "agent": "evolution",
            "summary": f"Évolution appliquée → {edit_result} | {push_result}",
            "decision": "DEPLOYED",
            "improvement": code_suggestion[:200] + "...",
            "confidence": 0.88,
            "stats_before": stats
        }
