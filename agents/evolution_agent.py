"""
🎯 EVOLUTION AGENT V5 — Agent d'évolution autonome & MAX TRADES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX 5 : Sécurité anti-écrasement de fichiers (plus de destruction accidentelle)
- Plus de write() complet sur les fichiers agents
- Édition limitée à un fichier de changelog dédié (evolution_changes.md)
- Mode "suggestion" au lieu d'édition directe
- Safety guard avant toute modification
"""

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

    # ======================== FIX 5 : _is_in_my_domain corrigé ========================
    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        evolution_keywords = ["évolution", "evolution", "amélioration", "upgrade", "améliorer", "modifier code", "auto-modif", "max trades"]
        return any(kw in q for kw in evolution_keywords)
    # ===========================================================================

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        # === UPGRADE V5 : Vérification stricte de spécialisation ===
        if not self._is_in_my_domain(question):
            return {
                "agent": self.name,
                "summary": f"⚠️ {self.name} hors de sa spécialité → ignoré",
                "confidence": 0.0,
                "recommendation": "HOLD - Vérifier rôle",
                "warning": "Hors domaine evolution"
            }

        # === UPGRADE V5 : Glossaire partagé pour zéro malentendu ===
        shared_glossary = context.get("shared_glossary", {})
        def explain(k): 
            return self.explain_term(k) or shared_glossary.get(k, k)

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

        # === TRIGGER AUTO APRÈS 100 TRADES ===
        trigger_100 = context.get("trigger") == "auto_evaluation_100_trades" or lesson_count % 100 == 0

        if trigger_100 and lesson_count > 0:
            print(f"[EVOLUTION] 🔥 TRIGGER 100 TRADES DÉTECTÉ ({lesson_count} leçons)")

            winrate = stats.get("winrate", 0)
            improvements = []

            if winrate < 0.98:
                improvements.append("Augmenter seuil de confiance à 0.99 dans l'orchestrator")

            # === FIX 5 : Édition SÉCURISÉE (non destructrice) ===
            edit_summary = "Changements proposés (non appliqués directement pour sécurité) :\n"
            for imp in improvements:
                edit_summary += f"• {imp}\n"

            # On écrit uniquement dans un fichier de changelog (sûr)
            safe_log = f"# Evolution auto après {lesson_count} leçons\n# Winrate : {winrate:.1%}\n\nPropositions :\n" + "\n".join([f"- {imp}" for imp in improvements])

            try:
                edit_result = self.edit_tool._run(
                    new_code=safe_log,
                    filename="evolution_changes.md"   # Fichier dédié et sûr
                )
            except Exception as e:
                edit_result = f"Warning: {e}"

            try:
                push_result = self.push_tool._run(
                    commit_message=f"EvolutionAgent — Auto-proposition après {lesson_count} trades | Winrate {winrate:.1%}"
                )
            except Exception as e:
                push_result = f"Warning: Push skipped: {e}"

            return {
                "agent": "evolution",
                "summary": f"✅ Cycle d'évolution terminé ({lesson_count} leçons) | {len(improvements)} améliorations proposées",
                "decision": "EVOLUTION_PROPOSED_SAFELY",
                "improvements": improvements,
                "edit_result": edit_result,
                "push_result": push_result,
                "confidence": 0.95,
                "glossary_used": True
            }

        # === Comportement normal (code original conservé) ===
        try:
            edit_result = self.edit_tool._run(
                new_code=f"# EvolutionAgent cycle | Leçons={lesson_count}\n",
                filename="evolution_marker.txt"
            )
        except Exception as e:
            edit_result = f"Warning: Edit skipped: {e}"

        try:
            push_result = self.push_tool._run(
                commit_message=f"EvolutionAgent — Cycle normal | Leçons={lesson_count}"
            )
        except Exception as e:
            push_result = f"Warning: Push skipped: {e}"

        return {
            "agent": "evolution",
            "summary": f"MAX TRADES activé | Leçons={lesson_count} | {edit_result} | {push_result}",
            "decision": "DEPLOYED_MAX_TRADES",
            "extreme_learning": extreme_learning,
            "confidence": 0.95,
            "glossary_used": True
        }
