"""
🎯 EVOLUTION AGENT V6 — Agent d'évolution autonome + Auto-réparation code + Git push sécurisé
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Rôle : surveille la qualité du code des agents, propose des améliorations,
les écrit dans evolution_changes.md et les pousse sur GitHub.
Mode suggestion uniquement (ne modifie pas directement bot.py pour éviter
les écrasements accidentels).

FIXES V6 :
- _is_in_my_domain complet incluant health/monitor/débat
- GitPushTool et EditBotFileTool avec fallback gracieux si unavailable
- Vrai analyse des agents pour proposer des améliorations ciblées
- Logs structurés (plus de print())
"""

import os
import time
import subprocess
from datetime import datetime
from typing import Dict, Any, List

from logging_config import logger

try:
    from agents.base_agent import BaseAgent, _KnowledgeBaseSingleton
except ImportError:
    class BaseAgent:
        def __init__(self, name="", role=""):
            self.name = name
            self.role = role
        def explain_term(self, t):
            return t
        async def safe_respond(self, q, c):
            return {}


# ────────────────────────────────────────────────────────────────────────────
# TOOLS LOCAUX (sans dépendance crewai)
# ────────────────────────────────────────────────────────────────────────────

class EditBotFileTool:
    """Écrit du contenu dans un fichier de manière sécurisée."""

    def _run(self, new_code: str = "", filename: str = "evolution_changes.md") -> str:
        # Sécurité : on n'écrase JAMAIS bot.py directement
        if filename in ("bot.py", "execution_engine.py", "memory.py"):
            return f"⚠️ Sécurité : modification directe de {filename} interdite. Utiliser evolution_changes.md"
        try:
            with open(filename, "a", encoding="utf-8") as f:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"\n\n## [{ts}] EvolutionAgent\n{new_code}\n")
            return f"✅ {filename} mis à jour"
        except Exception as e:
            return f"⚠️ Edit skipped: {e}"


class GitPushTool:
    """Pousse les changements sur Git de manière sécurisée."""

    def _run(self, commit_message: str = "EvolutionAgent auto-commit") -> str:
        try:
            # Vérification que git est disponible
            result = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                return "⚠️ Git non disponible"

            # N'ajoute que les fichiers non-critiques
            subprocess.run(
                ["git", "add", "evolution_changes.md", "evolution_marker.txt"],
                capture_output=True, timeout=10
            )
            commit = subprocess.run(
                ["git", "commit", "-m", commit_message],
                capture_output=True, text=True, timeout=15
            )
            if "nothing to commit" in commit.stdout:
                return "ℹ️ Rien à committer"
            # FIX: Vérification minimale avant push
            # Refus si le message de commit contient des patterns dangereux
            DANGEROUS_PATTERNS = [
                "EXTREME_LEARNING", "bypass", "veto ignoré",
                "disable risk", "force_max", "kelly = 1.0",
            ]
            commit_lower = commit_message.lower()
            if any(p.lower() in commit_lower for p in DANGEROUS_PATTERNS):
                logger.warning(f"[EVOLUTION] 🛑 Push bloqué — pattern dangereux détecté: {commit_message[:80]}")
                return f"🛑 Push bloqué (pattern risqué): {commit_message[:80]}"

            push = subprocess.run(
                ["git", "push"],
                capture_output=True, text=True, timeout=30
            )
            if push.returncode == 0:
                logger.info(f"[EVOLUTION] ✅ Push validé: {commit_message[:80]}")
                return f"✅ Push réussi : {commit_message}"
            return f"⚠️ Push échoué: {push.stderr[:100]}"
        except Exception as e:
            return f"Warning: Push skipped: {e}"


# ────────────────────────────────────────────────────────────────────────────
# AGENT
# ────────────────────────────────────────────────────────────────────────────

class EvolutionAgent(BaseAgent):

    def __init__(self, orchestrator=None):
        super().__init__(
            name="evolution",
            role=(
                "Agent d'évolution autonome — surveille le code des agents, propose des "
                "améliorations ciblées, les documente et les pousse sur Git"
            )
        )
        self.orchestrator = orchestrator
        self.edit_tool    = EditBotFileTool()
        self.push_tool    = GitPushTool()
        self._last_eval_ts = 0
        self._eval_interval = 300  # évalue toutes les 5 min max

    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        keywords = [
            # Rôle principal
            "évolution", "evolution", "amélioration", "upgrade", "améliorer",
            "modifier code", "auto-modif", "max trades", "code", "agent",
            # Santé/watchdog — envoyés par l'Orchestrator
            "monitor", "health", "santé", "watchdog", "immune",
            "repair", "répare", "surveillance", "anomalie",
            # Débat collectif
            "raffine", "synthèse", "débat", "cerveau collectif",
            "final decision", "trade ou no trade",
        ]
        return any(kw in q for kw in keywords)

    def _analyze_agent_performance(self, memory: dict) -> List[Dict]:
        """Analyse les performances des agents pour identifier les améliorations."""
        improvements = []

        # Analyse basée sur les stats mémoire
        trades = memory.get("trades", []) if isinstance(memory, dict) else []
        lessons = memory.get("lessons", []) if isinstance(memory, dict) else []

        total   = len(trades)
        wins    = [t for t in trades if isinstance(t, dict) and t.get("pnl", 0) > 0]
        winrate = len(wins) / total if total > 0 else 0.0

        if total >= 20 and winrate < 0.55:
            improvements.append({
                "agent":    "TraderAgent",
                "issue":    f"Winrate bas : {winrate:.1%} ({total} trades)",
                "proposal": "Augmenter le seuil de confiance minimum de 99% → réduire faux positifs",
                "priority": "HIGH",
            })

        if total >= 50 and winrate >= 0.80:
            improvements.append({
                "agent":    "TraderAgent",
                "issue":    "Winrate excellent",
                "proposal": "Considérer augmenter légèrement la taille des positions",
                "priority": "LOW",
            })

        lesson_count = len(lessons)
        if lesson_count > 500 and lesson_count % 100 == 0:
            improvements.append({
                "agent":    "LearningAgent",
                "issue":    f"{lesson_count} leçons accumulées",
                "proposal": "Compresser les leçons anciennes pour optimiser les requêtes",
                "priority": "MEDIUM",
            })

        return improvements

    def _write_improvements(self, improvements: List[Dict], lesson_count: int, winrate: float) -> str:
        """Écrit les propositions d'amélioration dans le fichier dédié."""
        if not improvements:
            return self.edit_tool._run(
                new_code=f"Cycle auto-évaluation | Leçons={lesson_count} | WR={winrate:.1%} | Aucune amélioration requise ✅",
                filename="evolution_changes.md"
            )

        content = f"Cycle auto-évaluation | Leçons={lesson_count} | WR={winrate:.1%}\n\n"
        for imp in improvements:
            content += (
                f"### [{imp['priority']}] {imp['agent']}\n"
                f"- Problème: {imp['issue']}\n"
                f"- Proposition: {imp['proposal']}\n\n"
            )
        return self.edit_tool._run(new_code=content, filename="evolution_changes.md")

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {
                "agent":          self.name,
                "summary":        "⚠️ EvolutionAgent hors spécialité → ignoré",
                "confidence":     0.0,
                "recommendation": "HOLD - Vérifier rôle",
                "warning":        "Hors domaine evolution",
            }

        shared_glossary = context.get("shared_glossary", {})
        def explain(k):
            return self.explain_term(k) or shared_glossary.get(k, k)

        memory = context.get("memory", {})
        if not isinstance(memory, dict):
            try:
                memory = memory.data if hasattr(memory, 'data') else {}
            except Exception:
                memory = {}

        # Récupération stats
        try:
            lesson_count = (
                self.orchestrator.learning.get_lesson_count()
                if self.orchestrator and hasattr(self.orchestrator, "learning")
                else len(memory.get("lessons", []))
            )
        except Exception:
            lesson_count = len(memory.get("lessons", [])) if isinstance(memory, dict) else 0

        trades   = memory.get("trades", []) if isinstance(memory, dict) else []
        total    = len(trades)
        wins     = [t for t in trades if isinstance(t, dict) and t.get("pnl", 0) > 0]
        winrate  = len(wins) / total if total > 0 else 0.0

        # Rate limiting
        now = time.time()
        if now - self._last_eval_ts < self._eval_interval:
            return {
                "agent":          self.name,
                "summary":        f"⏳ EvolutionAgent en cooldown | Leçons={lesson_count} | WR={winrate:.1%}",
                "confidence":     0.90,
                "recommendation": "HOLD - Cooldown actif",
                "lesson_count":   lesson_count,
                "winrate":        winrate,
                "glossary_used":  True,
            }
        self._last_eval_ts = now

        # Analyse et amélioration
        improvements = self._analyze_agent_performance(memory)
        edit_result  = self._write_improvements(improvements, lesson_count, winrate)

        # Push seulement si améliorations trouvées
        push_result = "ℹ️ Aucune amélioration → push non nécessaire"
        if improvements:
            push_result = self.push_tool._run(
                commit_message=f"EvolutionAgent — {len(improvements)} améliorations proposées | Leçons={lesson_count} | WR={winrate:.1%}"
            )
            logger.info(f"[EVOLUTION] {push_result}")

        summary = (
            f"🔄 EvolutionAgent | Leçons={lesson_count} | WR={winrate:.1%} | "
            f"{len(improvements)} améliorations proposées | Push: {push_result[:50]}"
        )

        return {
            "agent":          self.name,
            "summary":        summary,
            "decision":       "EVOLUTION_CYCLE_COMPLETE",
            "improvements":   improvements,
            "edit_result":    edit_result,
            "push_result":    push_result,
            "lesson_count":   lesson_count,
            "winrate":        winrate,
            "confidence":     0.92,
            "recommendation": "Vérifier evolution_changes.md pour les propositions",
            "glossary_used":  True,
        }
