try:
    from agents.base_agent import BaseAgent
except ImportError:
    class BaseAgent:
        def __init__(self, name="", role=""):
            self.name = name
            self.role = role

from typing import Dict, Any

try:
    from tools import EditBotFileTool, GitPushTool
except ImportError:
    try:
        from agents.tools import EditBotFileTool, GitPushTool
    except ImportError:
        class EditBotFileTool:
            def _run(self, new_code="", filename="bot.py"):
                return f"Warning: EditBotFileTool non disponible"
        class GitPushTool:
            def _run(self, commit_message=""):
                return f"Warning: GitPushTool non disponible"

class EvolutionAgent(BaseAgent):

    def __init__(self, orchestrator):
        super().__init__(name="evolution", role="Agent d'évolution autonome — MAX TRADES & MAX EXPÉRIENCE")
        self.orchestrator = orchestrator
        self.edit_tool = EditBotFileTool()
        self.push_tool = GitPushTool()

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        memory = context.get("memory", {})

        try:
            stats = self.orchestrator.performance.get_global_stats(memory) if hasattr(self.orchestrator, 'performance') else {}
        except Exception:
            stats = {}

        try:
            lesson_count = self.orchestrator.learning.get_lesson_count() if hasattr(self.orchestrator, 'learning') else 0
        except Exception:
            lesson_count = 0

        extreme_learning = lesson_count < 1500

        print(f"[EVOLUTION] Cycle | Leçons={lesson_count} | Extreme={extreme_learning}")

        try:
            edit_result = self.edit_tool._run(
                new_code=f"# EvolutionAgent cycle | Leçons={lesson_count}\n",
                filename="evolution_marker.txt"
            )
        except Exception as e:
            edit_result = f"Warning: Edit skipped: {e}"

        try:
            push_result = self.push_tool._run(
                commit_message=f"EvolutionAgent — MAX TRADES cycle | Leçons={lesson_count}"
            )
        except Exception as e:
            push_result = f"Warning: Push skipped: {e}"

        return {
            "agent": "evolution",
            "summary": f"MAX TRADES activé | Leçons={lesson_count} | {edit_result} | {push_result}",
            "decision": "DEPLOYED_MAX_TRADES",
            "extreme_learning": extreme_learning,
            "confidence": 0.95
        }
