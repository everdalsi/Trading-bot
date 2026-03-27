import asyncio
import time
import traceback
import threading
import datetime
import random

try:
    from crewai import Crew, Task, Agent
    from crewai.tools import BaseTool
    CREWAI_AVAILABLE = True
except ImportError:
    CREWAI_AVAILABLE = False
    print("⚠️ CrewAI non disponible — self_improvement en mode dégradé")

try:
    from tools import EditBotFileTool, GitPushTool
except ImportError:
    try:
        from agents.tools import EditBotFileTool, GitPushTool
    except ImportError:
        class EditBotFileTool:
            def _run(self, **kwargs): return "⚠️ Tool non disponible"
        class GitPushTool:
            def _run(self, **kwargs): return "⚠️ Tool non disponible"

try:
    from prometheus_client import start_http_server, Counter, Gauge, Histogram
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    print("⚠️ Prometheus non disponible")
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

# Métriques Prometheus
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


# ── Gestion intelligente des quotas Groq ────────────────────────
class QuotaManager:
    def __init__(self):
        self.current_model = "groq/llama-3.1-8b-instant"
        self.last_rate_limit = 0
        self.rate_limit_count = 0

    def get_model(self):
        # Toujours le modèle léger — stable sur Groq
        self.current_model = "groq/llama-3.1-8b-instant"
        return self.current_model

    def handle_rate_limit(self):
        self.rate_limit_count += 1
        self.last_rate_limit = time.time()
        print(f"[QUOTA MANAGER] Rate limit détecté ({self.rate_limit_count})")

quota_manager = QuotaManager()


# ── create_improvement_crew conservé mais NON UTILISÉ ───────────
# (CrewAI + llama-3.1-8b-instant = tool_use_failed sur Groq)
# Conservé ici pour référence / future migration vers un modèle compatible.
def create_improvement_crew():
    """
    DÉSACTIVÉ — CrewAI génère des appels d'outils (function calling) que
    llama-3.1-8b-instant refuse avec tool_use_failed sur Groq.
    On passe directement par EvolutionAgent dans start_self_improvement_loop.
    """
    return None


# ── Helpers ─────────────────────────────────────────────────────
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
    """Lance EvolutionAgent.respond() dans une nouvelle event loop (thread-safe)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        ctx = {
            "memory": memory,
            "main_objective": MAIN_OBJECTIVE,
        }
        try:
            stats = orchestrator.performance.get_global_stats(memory) \
                    if hasattr(orchestrator, 'performance') else {}
            ctx["drawdown"] = stats.get("degraded", False)
        except Exception:
            pass

        result = loop.run_until_complete(
            evolution_agent.respond("Lance un cycle d'évolution MAX TRADES", ctx)
        )
        return result
    finally:
        try:
            loop.close()
        except Exception:
            pass


# ── Boucle principale ────────────────────────────────────────────
def start_self_improvement_loop(orchestrator):
    """
    Boucle d'auto-amélioration.
    Utilise EvolutionAgent directement (pas CrewAI) pour éviter
    le bug tool_use_failed de llama-3.1-8b-instant sur Groq.
    """
    print("[SELF-IMPROVEMENT] ✅ AUTONOMIE TOTALE — EvolutionAgent direct (bypass CrewAI)")
    cycle = 0
    last_rate_limit = 0

    while True:
        cycle += 1
        now_str = datetime.datetime.now().strftime('%H:%M:%S')
        print(f"[SELF-IMPROVEMENT] Cycle #{cycle} - {now_str}")

        try:
            if EvolutionAgent is None:
                # Pas d'agent disponible → on log et on attend
                print(f"[SELF-IMPROVEMENT] EvolutionAgent non disponible, cycle #{cycle} ignoré")
            else:
                memory = getattr(orchestrator, 'memory', {})
                evo    = EvolutionAgent(orchestrator)

                result = _run_evolution_cycle_sync(evo, orchestrator, memory, cycle)

                summary = result.get('summary', 'ok') if isinstance(result, dict) else str(result)[:80]
                print(f"[SELF-IMPROVEMENT] ✅ Cycle #{cycle} terminé — {summary}")

                evolution_cycles_total.inc()
                evolution_success_total.inc()

                # Mise à jour métriques Prometheus
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

        # Pause entre cycles (plus courte si pas de rate limit récent)
        base_sleep = 90 if (time.time() - last_rate_limit < 900) else 60
        time.sleep(base_sleep + random.uniform(10, 20))
