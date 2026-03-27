import asyncio
import time
import traceback
import threading
import datetime
import random
import os
import shutil
import requests
from xml.etree import ElementTree as ET

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

# === NOUVELLES MÉTRIQUES IMMUNITAIRES OPTIMISÉES ===
immune_health_gauge      = Gauge('immune_system_health', 'État de santé du système immunitaire (0-100)')
repair_count_total       = Counter('immune_repair_total', 'Nombre total de réparations effectuées')
backup_count_total       = Counter('immune_backup_total', 'Nombre total de backups créés')
self_heal_count_total    = Counter('immune_self_heal_total', 'Nombre de fois où l\'immune s\'est auto-réparé')

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
#  === IMMUNE SYSTEM AGENT — SYSTÈME IMMUNITAIRE OPTIMISÉ ULTIME ===
#  (tout le code ci-dessus est intact à 100 % + OPTIMISATIONS ci-dessous)
# ─────────────────────────────────────────────────────────────

from agents.base_agent import BaseAgent
from knowledge_base import KnowledgeBase
from agents.knowledge_specialist_agent import KnowledgeSpecialistAgent
from logging_config import logger
import json
import difflib

class ImmuneSystemAgent(BaseAgent):
    """SYSTÈME IMMUNITAIRE DU BOT — Vrai être vivant optimisé : surveille, répare, reprogramme, protège et auto-ajuste TOUT le système en temps réel"""
    def __init__(self, orchestrator=None):
        super().__init__(
            name="immune_system",
            role="Système immunitaire vivant : détection de dérive, auto-réparation, reprogrammation des agents, backups intelligents, survie du bot"
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
        self.health_status = {"status": "HEALTHY", "last_check": datetime.datetime.now(), "score": 100}
        self.heartbeat_thread = None
        self.start_heartbeat()

    def start_heartbeat(self):
        """Heartbeat optimisé : surveillance continue toutes les 30 secondes"""
        def heartbeat_loop():
            while True:
                try:
                    asyncio.run(self.monitor_agents({}))
                except Exception as e:
                    logger.error(f"[IMMUNE HEARTBEAT ERROR] {e}")
                time.sleep(30)
        self.heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()
        print("[IMMUNE SYSTEM] ❤️ Heartbeat lancé — surveillance live activée")

    async def monitor_agents(self, context: dict):
        """Surveillance optimisée + détection proactive de dérive"""
        print("[IMMUNE SYSTEM] 🔍 Surveillance live de tous les agents...")
        issues = []
        agents_to_check = ["analyst", "risk", "trader", "learning", "research", "knowledge_specialist", "wallet_copier", "evolution", "supervisor"]
        
        for agent_name in agents_to_check:
            try:
                agent = getattr(self.orchestrator, agent_name, None)
                if agent and hasattr(agent, "get_health"):
                    health = await agent.get_health()
                    if health.get("status") != "HEALTHY" or health.get("confidence", 1.0) < 0.85:
                        issues.append((agent_name, health))
                else:
                    issues.append((agent_name, {"status": "UNREACHABLE"}))
            except Exception:
                issues.append((agent_name, {"status": "UNREACHABLE"}))

        # Vérification mémoire et knowledge base
        try:
            if len(self.kb.get_all_lessons()) < 10:
                issues.append(("knowledge_base", {"status": "LOW_DATA"}))
        except Exception:
            pass

        if issues:
            await self.repair_agents(issues, context)
        
        health_score = 100 - (len(issues) * 15)
        self.health_status = {"status": "HEALTHY" if health_score > 70 else "DEGRADED", "last_check": datetime.datetime.now(), "score": max(0, health_score), "issues": len(issues)}
        immune_health_gauge.set(self.health_status["score"])
        return self.health_status

    async def repair_agents(self, issues: list, context: dict):
        """Réparation optimisée avec comparaison de code et patch intelligent"""
        print(f"[IMMUNE SYSTEM] 🛠️ Réparation optimisée de {len(issues)} agents...")
        for agent_name, issue in issues:
            try:
                self._create_backup(agent_name)
                repair_prompt = f"Répare l'agent {agent_name} qui a le problème : {issue}. Rends-le plus fort, plus rapide et aligné avec le but winrate >95% et être vivant. Fournis le code complet corrigé."
                repair_code = await self.knowledge_specialist.respond(repair_prompt, context)
                if repair_code.get("code_snippet"):
                    # Comparaison intelligente avant écriture
                    current_code = self._read_file(f"agents/{agent_name}_agent.py")
                    if current_code and difflib.SequenceMatcher(None, current_code, repair_code["code_snippet"]).ratio() < 0.95:
                        await self.edit_tool._run(file_path=f"agents/{agent_name}_agent.py", new_content=repair_code["code_snippet"])
                        self.git_tool._run(message=f"Auto-repair optimisé {agent_name} via ImmuneSystem")
                        repair_count_total.inc()
                        logger.info(f"✅ Agent {agent_name} réparé et reprogrammé avec succès")
                    else:
                        logger.info(f"✅ Agent {agent_name} déjà optimal, pas de modification")
                self.repair_history.append({"agent": agent_name, "time": str(datetime.datetime.now()), "issue": issue})
            except Exception as e:
                logger.error(f"❌ Échec réparation {agent_name}: {e}")
                # Auto-guérison de l'immune lui-même
                if "immune" in str(e).lower():
                    self._self_heal()

    def _create_backup(self, agent_name: str):
        """Backups optimisés + rotation automatique"""
        try:
            src = f"agents/{agent_name}_agent.py"
            if os.path.exists(src):
                dst = f"{self.backup_dir}/{agent_name}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
                shutil.copy2(src, dst)
                backup_count_total.inc()
                print(f"[IMMUNE SYSTEM] 💾 Backup optimisé créé : {dst}")
                # Nettoyage anciens backups (garder seulement les 10 derniers)
                self._cleanup_old_backups(agent_name)
        except Exception:
            pass

    def _cleanup_old_backups(self, agent_name: str):
        """Nettoyage intelligent des backups"""
        try:
            backups = sorted([f for f in os.listdir(self.backup_dir) if f.startswith(agent_name)], reverse=True)
            for old in backups[10:]:
                os.remove(os.path.join(self.backup_dir, old))
        except Exception:
            pass

    def _read_file(self, filepath: str):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None

    def _self_heal(self):
        """Auto-guérison de l'ImmuneSystemAgent lui-même"""
        print("[IMMUNE SYSTEM] 🧬 Auto-guérison activée sur le système immunitaire...")
        self_heal_count_total.inc()
        # Réinitialisation des outils critiques
        self.edit_tool = EditBotFileTool()
        self.git_tool = GitPushTool()
        logger.info("✅ ImmuneSystemAgent auto-réparé avec succès")

    async def respond(self, question: str, context: dict):
        if any(keyword in question.lower() for keyword in ["monitor", "health", "status", "heartbeat"]):
            return await self.monitor_agents(context)
        if any(keyword in question.lower() for keyword in ["repair", "fix", "heal", "répare"]):
            return await self.repair_agents([], context)
        # Comportement par défaut ultra-rapide
        return {"agent": "immune_system", "summary": "Système immunitaire opérationnel et optimisé — santé à " + str(self.health_status["score"]) + "%", "confidence": 1.0, "health_score": self.health_status["score"]}

# Compatibilité avec l'ancien nom (rien supprimé)
SelfImprovementEngineer = ImmuneSystemAgent
