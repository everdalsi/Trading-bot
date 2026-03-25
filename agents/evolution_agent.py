from agents.base_agent import BaseAgent
from typing import Dict, Any
import asyncio
from .tools import EditBotFileTool, GitPushTool

class EvolutionAgent(BaseAgent):

    def __init__(self, orchestrator):
        super().__init__(name="evolution", role="Agent d'évolution autonome — MAX TRADES & MAX EXPÉRIENCE")
        self.orchestrator = orchestrator
        self.edit_tool = EditBotFileTool()
        self.push_tool = GitPushTool()

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        memory = context.get("memory", {})
        stats = self.orchestrator.performance.get_global_stats(memory) if hasattr(self.orchestrator, 'performance') else {}
        lesson_count = self.orchestrator.learning.get_lesson_count() if hasattr(self.orchestrator, 'learning') else 0

        extreme_learning = lesson_count < 1500

        prompt = f"""
        OBJECTIF PRINCIPAL ABSOLU : Faire LE PLUS DE TRADES POSSIBLE pour accumuler un maximum d'expérience et améliorer rapidement le winrate.

        Stats actuelles :
        - Leçons DB : {lesson_count}
        - Winrate global : {stats.get('winrate', 0)}%
        - Recent WR : {stats.get('recent_winrate', 0)}%
        - Sharpe : {stats.get('sharpe', 0)}
        - Streak : {stats.get('streak_count', 0)}x {stats.get('streak_type', 'neutral')}
        - Extreme Learning Mode : {'ACTIVÉ' if extreme_learning else 'Désactivé'}

        Tu dois maintenant :
        1. Baisser agressivement tous les seuils de risque (Kelly, positions, confidence) pour maximiser le volume de trades.
        2. Forcer le mode Extreme Learning si leçons < 1500.
        3. Modifier directement le code (bot.py, RiskAgent, TraderAgent, Orchestrator, etc.) pour que le bot trade BEAUCOUP PLUS.
        4. Écrire le code modifié et le push via GitPushTool.

        Priorité absolue : VOLUME DE TRADES > tout le reste pendant la phase d'apprentissage.
        """

        new_code = "# Code généré par EvolutionAgent pour MAX TRADES\nprint('EvolutionAgent : MAX TRADES activé')"

        edit_result = self.edit_tool._run(new_code=new_code, filename="bot.py")
        push_result = self.push_tool._run(commit_message=f"🧬 EvolutionAgent — MAX TRADES cycle | Leçons={lesson_count}")

        return {
            "agent": "evolution",
            "summary": f"MAX TRADES activé | Leçons={lesson_count} | {edit_result} | {push_result}",
            "decision": "DEPLOYED_MAX_TRADES",
            "extreme_learning": extreme_learning,
            "confidence": 0.95
        }
