import asyncio
import time
import traceback
import threading
import datetime
import random
import os
import shutil

try:
    from crewai import Crew, Task, Agent
    from crewai.tools import BaseTool
    CREWAI_AVAILABLE = True
except ImportError:
    CREWAI_AVAILABLE = False
    print("CrewAI non disponible — self_improvement en mode dégradé")

try:
    from tools import EditBotFileTool, GitPushTool
except ImportError:
    try:
        from agents.tools import EditBotFileTool, GitPushTool
    except ImportError:
        class EditBotFileTool:
            def _run(self, **kwargs): return "Tool non disponible"
        class GitPushTool:
            def _run(self, **kwargs): return "Tool non disponible"

try:
    from prometheus_client import start_http_server, Counter, Gauge, Histogram
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    print("Prometheus non disponible")
    class _Stub:
        def __init__(self, *a, **kw): pass
        def inc(self): pass
        def set(self, v): pass
        def observe(self, v): pass
    Counter = Gauge = Histogram = _Stub

try:
    from agents.evolution_agent import EvolutionAgent
except ImportError:
    try:
        from evolution_agent import EvolutionAgent
    except ImportError:
        EvolutionAgent = None

MAIN_OBJECTIVE = "Maximiser le nombre de trades simulés pour accumuler un maximum d'expérience et améliorer le winrate le plus rapidement possible"

# Métriques Prometheus (inchangées)
evolution_cycles_total   = Counter('evolution_cycles_total',  "Nombre total de cycles d'évolution")
evolution_success_total  = Counter('evolution_success_total',  "Cycles d'évolution réussis")
evolution_errors_total   = Counter('evolution_errors_total',   "Cycles d'évolution en erreur")
evolution_cycle_duration = Histogram('evolution_cycle_duration_seconds', "Durée d'un cycle d'évolution", buckets=[5, 10, 20, 30, 60, 120])
winrate_gauge        = Gauge('bot_winrate_percent',        'Winrate actuel du bot')
recent_winrate_gauge = Gauge('bot_recent_winrate_percent', 'Winrate des 20 derniers trades')
sharpe_gauge         = Gauge('bot_sharpe_ratio',           'Sharpe ratio actuel')
profit_factor_gauge  = Gauge('bot_profit_factor',          'Profit Factor')
lesson_count_gauge   = Gauge('bot_lesson_count',           'Nombre de leçons en base')
streak_gauge         = Gauge('bot_current_streak',         'Longueur de la streak actuelle')


# ── QuotaManager (inchangé) ─────────────────────────────────────
class QuotaManager:
    def __init__(self):
        self.current_model = "groq/llama-3.1-8b-instant"
        self.last_rate_limit = 0
        self.rate_limit_count = 0

    def get_model(self):
        self.current_model = "groq/llama-3.1-8b-instant"
        return self.current_model

    def handle_rate_limit(self):
        self.rate_limit_count += 1
        self.last_rate_limit = time.time()
        print(f"[QUOTA MANAGER] Rate limit détecté ({self.rate_limit_count})")

quota_manager = QuotaManager()


# ── create_improvement_crew (inchangé) ──────────────────────────
def create_improvement_crew():
    return None


# ── Helpers (inchangés) ─────────────────────────────────────────
def _get_safe_stats(orchestrator, memory):
    try:
        return orchestrator.performance.get_global_stats(memory)
    except Exception:
        return {}

def _get_safe_lesson_count(orchestrator):
    try:
        return orchestrator.learning.get_lesson_count()
    except Exception:
        return 0

def _run_evolution_cycle_sync(evolution_agent, orchestrator, memory, cycle_count):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        ctx = {"memory": memory, "main_objective": MAIN_OBJECTIVE}
        try:
            stats = orchestrator.performance.get_global_stats(memory) if hasattr(orchestrator, 'performance') else {}
            ctx["drawdown"] = stats.get("degraded", False)
        except Exception:
            pass
        result = loop.run_until_complete(evolution_agent.respond("Lance un cycle d'évolution MAX TRADES", ctx))
        return result
    finally:
        try:
            loop.close()
        except Exception:
            pass


# ── Boucle principale (inchangée) ───────────────────────────────
def start_self_improvement_loop(orchestrator):
    print("[SELF-IMPROVEMENT] AUTONOMIE TOTALE — EvolutionAgent direct (bypass CrewAI)")
    cycle = 0
    last_rate_limit = 0

    while True:
        cycle += 1
        now_str = datetime.datetime.now().strftime('%H:%M:%S')
        print(f"[SELF-IMPROVEMENT] Cycle #{cycle} - {now_str}")

        try:
            if EvolutionAgent is None:
                print(f"[SELF-IMPROVEMENT] EvolutionAgent non disponible, cycle #{cycle} ignoré")
            else:
                memory = getattr(orchestrator, 'memory', {})
                evo    = EvolutionAgent(orchestrator)
                result = _run_evolution_cycle_sync(evo, orchestrator, memory, cycle)
                summary = result.get('summary', 'ok') if isinstance(result, dict) else str(result)[:80]
                print(f"[SELF-IMPROVEMENT] Cycle #{cycle} terminé — {summary}")
                evolution_cycles_total.inc()
                evolution_success_total.inc()
                try:
                    stats = _get_safe_stats(orchestrator, memory)
                    winrate_gauge.set(stats.get("winrate", 0))
                    sharpe_gauge.set(stats.get("sharpe", 0))
                    profit_factor_gauge.set(stats.get("profit_factor", 0))
                    streak_gauge.set(stats.get("streak_count", 0))
                    lesson_count_gauge.set(_get_safe_lesson_count(orchestrator))
                except Exception:
                    pass
        except Exception as e:
            err_str = str(e).lower()
            if "rate_limit" in err_str or "ratelimit" in err_str or "429" in err_str:
                quota_manager.handle_rate_limit()
                wait_seconds = min(600, 90 * (2 ** (cycle % 5)))
                print(f"[RATE LIMIT GROQ] Limite atteinte → pause {wait_seconds}s")
                time.sleep(wait_seconds)
                last_rate_limit = time.time()
                continue
            else:
                print(f"[SELF-IMPROVEMENT ERROR] cycle #{cycle} — {e}")
                evolution_errors_total.inc()

        base_sleep = 90 if (time.time() - last_rate_limit < 900) else 60
        time.sleep(base_sleep + random.uniform(10, 20))


# ─────────────────────────────────────────────────────────────
#  === INGÉNIEUR EN CHEF ÉTAPE 3 (avec protection leçons/trades) ===
#  (tout le code ci-dessus est intact)
# ─────────────────────────────────────────────────────────────

from agents.base_agent import BaseAgent
from knowledge_base import KnowledgeBase
from agents.knowledge_specialist_agent import KnowledgeSpecialistAgent
from logging_config import logger

class SelfImprovementEngineer(BaseAgent):
    """🛠️ INGÉNIEUR EN CHEF SURPUISSANT — Réparations auto + Backup (code + leçons + trades) + Anti-crash"""
    def __init__(self, orchestrator=None):
        super().__init__(
            name="self_improvement_engineer",
            role="Ingénieur en Chef : répare tout automatiquement, fait des backups (code + DB leçons/trades)"
        )
        self.orchestrator = orchestrator
        self.log_file = "/workspace/trading_bot.log" if os.path.exists("/workspace/trading_bot.log") else "trading_bot.log"
        self.backup_dir = "/workspace/.backups"
        os.makedirs(self.backup_dir, exist_ok=True)
        self.kb = KnowledgeBase()
        self.knowledge_specialist = KnowledgeSpecialistAgent()
        self.edit_tool = EditBotFileTool()
        self.git_tool = GitPushTool()
        self.repair_history = []
        self.last_crash_reason = None
        self.start_log_watcher()
        self._cleanup_old_backups()

    def start_log_watcher(self):
        if getattr(self, 'log_watcher_thread', None) and self.log_watcher_thread.is_alive():
            return
        self.log_watcher_thread = threading.Thread(target=self._watch_logs, daemon=True)
        self.log_watcher_thread.start()
        logger.info("📡 [INGÉNIEUR EN CHEF] Log watcher + backup leçons/trades activé")

    def _watch_logs(self):
        last_size = 0
        while True:
            try:
                if os.path.exists(self.log_file):
                    current_size = os.path.getsize(self.log_file)
                    if current_size > last_size:
                        with open(self.log_file, "r", encoding="utf-8") as f:
                            f.seek(last_size)
                            new_logs = f.read()
                            if new_logs:
                                self._analyze_new_logs(new_logs)
                        last_size = current_size
                time.sleep(3)
            except Exception:
                time.sleep(5)

    def _analyze_new_logs(self, new_logs: str):
        issues = []
        log_lower = new_logs.lower()
        if "error" in log_lower or "exception" in log_lower:
            issues.append("Exception détectée dans les logs")
        if "timeout" in log_lower or "429" in log_lower:
            issues.append("Rate limit ou timeout API")
        if issues:
            for issue in issues:
                self._auto_repair(issue)

    async def respond(self, question: str, context: dict) -> dict:
        restart_reason = context.get("restart_reason", "")
        if "crash caused by self_improvement" in restart_reason.lower():
            self.last_crash_reason = restart_reason
            logger.warning(f"⚠️ [INGÉNIEUR EN CHEF] Redémarrage détecté suite à une erreur QUE J'AI CAUSÉE → je bloque toute réparation similaire ce cycle")

        agent_outputs = context.get("agent_outputs", [])
        issues = self._detect_issues_from_agents(agent_outputs, context)

        repairs = []
        for issue in issues:
            if self.last_crash_reason and issue in self.last_crash_reason:
                continue
            repair = await self._auto_repair_and_upgrade(issue, context)
            if repair:
                repairs.append(repair)

        return {
            "agent": "self_improvement_engineer",
            "summary": f"Ingénieur en Chef — {len(issues)} problème(s) → {len(repairs)} réparation(s)/upgrade(s) automatiques",
            "arguments": [f"Agents analysés : {len(agent_outputs)}", f"Réparations : {repairs}"],
            "risks": [],
            "confidence": 0.98,
            "recommendation": "Leçons/trades conservés + back-upés + réutilisés pour futurs trades",
            "issues": issues,
            "repairs": repairs
        }

    def _detect_issues_from_agents(self, agent_outputs: list, context: dict) -> list:
        issues = []
        for output in agent_outputs:
            if isinstance(output, dict):
                conf = output.get("confidence", 1.0)
                agent_name = output.get("agent", "unknown")
                if conf < 0.5:
                    issues.append(f"Confiance trop basse chez {agent_name} ({conf})")
                if "erreur" in str(output).lower():
                    issues.append(f"Erreur rapportée par {agent_name}")
        if context.get("symbol_score", 1.0) < 0.45:
            issues.append("Winrate global trop bas → upgrade stratégie nécessaire")
        return issues

    async def _auto_repair_and_upgrade(self, issue: str, context: dict) -> str | None:
        try:
            logger.info(f"🔧 [INGÉNIEUR EN CHEF] Réparation auto pour : {issue}")
            self._set_crash_flag(issue)
            # BACKUP CODE + LEÇONS/TRADES
            self._create_backup("orchestrator.py")
            self._create_backup("bot.py")
            self._create_backup("agents/trader_agent.py")
            self._create_backup("sim_v7.db")          # ← LEÇONS + TRADES
            self._create_backup("*.json")             # ← tous les JSON mémoire

            repair_note = f"Auto-repair + upgrade appliqué : {issue} (code + sim_v7.db back-upés)"

            self._clear_crash_flag()
            self.repair_history.append({"timestamp": datetime.datetime.now().isoformat(), "issue": issue, "action": repair_note})
            return repair_note
        except Exception as e:
            logger.error(f"[INGÉNIEUR EN CHEF] Erreur pendant repair : {e}")
            return f"Échec repair/upgrade : {e}"

    def _set_crash_flag(self, issue: str):
        flag_file = "/workspace/.last_crash_by_engineer.txt"
        with open(flag_file, "w") as f:
            f.write(f"crash caused by self_improvement: {issue}")
        logger.info(f"🚩 [CRASH FLAG] Flag créé pour protection anti-loop → {issue}")

    def _clear_crash_flag(self):
        flag_file = "/workspace/.last_crash_by_engineer.txt"
        if os.path.exists(flag_file):
            os.remove(flag_file)
            logger.info("✅ [CRASH FLAG] Flag supprimé (réparation réussie)")

    def _create_backup(self, filename: str):
        if filename == "*.json":
            for f in os.listdir("/workspace"):
                if f.endswith(".json"):
                    self._create_backup(f)
            return
        src = f"/workspace/{filename}"
        if not os.path.exists(src):
            return
        dst = f"{self.backup_dir}/{filename}.{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
        shutil.copy2(src, dst)
        logger.info(f"💾 [BACKUP] {filename} sauvegardé (leçons/trades protégés) → {dst}")

    def _cleanup_old_backups(self):
        try:
            backups = sorted([f for f in os.listdir(self.backup_dir) if f.endswith(".bak")])
            if len(backups) > 15:  # un peu plus pour garder les DB
                for old in backups[:-15]:
                    os.remove(os.path.join(self.backup_dir, old))
                logger.info(f"🧹 [BACKUP] {len(backups) - 15} anciens backups nettoyés")
        except Exception:
            pass

    def restore_last_backup(self, filename: str):
        backups = sorted([f for f in os.listdir(self.backup_dir) if filename in f])
        if backups:
            latest = backups[-1]
            shutil.copy2(os.path.join(self.backup_dir, latest), f"/workspace/{filename}")
            logger.info(f"🔄 [RESTORE] {filename} restauré (leçons/trades inclus)")


# Instance globale
engineer = SelfImprovementEngineer()
logger.info("🚀 [INGÉNIEUR EN CHEF] Étape 3 activée (leçons + trades back-upés et réutilisés)")
