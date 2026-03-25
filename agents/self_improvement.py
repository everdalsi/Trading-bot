import asyncio
import time
import traceback
import threading

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
    # Stubs pour éviter les NameError
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

MAIN_OBJECTIVE = "Maximiser le nombre de trades simulés pour accumuler un maximum d’expérience et améliorer le winrate le plus rapidement possible"

# Métriques Prometheus
evolution_cycles_total  = Counter('evolution_cycles_total',  "Nombre total de cycles d'évolution")
evolution_success_total = Counter('evolution_success_total',  "Cycles d'évolution réussis")
evolution_errors_total  = Counter('evolution_errors_total',   "Cycles d'évolution en erreur")
evolution_cycle_duration = Histogram('evolution_cycle_duration_seconds', "Durée d'un cycle d'évolution", buckets=[5, 10, 20, 30, 60, 120])
winrate_gauge        = Gauge('bot_winrate_percent',        'Winrate actuel du bot')
recent_winrate_gauge = Gauge('bot_recent_winrate_percent', 'Winrate des 20 derniers trades')
sharpe_gauge         = Gauge('bot_sharpe_ratio',           'Sharpe ratio actuel')
profit_factor_gauge  = Gauge('bot_profit_factor',          'Profit Factor')
lesson_count_gauge   = Gauge('bot_lesson_count',           'Nombre de leçons en base')
streak_gauge         = Gauge('bot_current_streak',         'Longueur de la streak actuelle')

# ── CrewAI improver (optionnel) ──────────────────────────────
def create_improvement_crew():
    if not CREWAI_AVAILABLE:
        return None
    try:
        improver = Agent(
            role="Senior Self-Improvement Engineer",
            goal="Améliorer constamment le bot et les agents à partir de l'objectif principal",
            backstory="Tu es un ingénieur IA autonome. Tu analyses les performances, proposes du code, le testes et déploies.",
            tools=[EditBotFileTool(), GitPushTool()],
            llm="groq/llama3-70b-8192",
            verbose=True,          # ← corrigé ici
            allow_code_execution=False
        )
        task = Task(
            description=f"""
Objectif principal du bot : {MAIN_OBJECTIVE}
Analyse les stats, identifie les faiblesses, propose des améliorations concrètes.
""",
            expected_output="Code modifié + message de commit + décision de déploiement",
            agent=improver
        )
        crew = Crew(agents=[improver], tasks=[task], verbose=True, memory=True, cache=True)
        return crew
    except Exception as e:
        print(f"[CREW] Création impossible: {e}")
        return None

async def run_self_improvement_cycle():
    """Cycle CrewAI async — ne bloque pas le thread principal"""
    print("🚀 [SELF-IMPROVEMENT] Lancement du cycle d'auto-amélioration…")
    try:
        crew = create_improvement_crew()
        if crew is None:
            print("⚠️ [SELF-IMPROVEMENT] CrewAI non disponible — cycle ignoré")
            return {"status": "skipped", "reason": "crewai_unavailable"}
        # Run dans un executor pour ne pas bloquer l'event loop
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, crew.kickoff)
        print("✅ [SELF-IMPROVEMENT] Cycle terminé :", result)
        return result
    except Exception as e:
        print(f"[SELF-IMPROVEMENT ERROR] {e}")
        return {"status": "error", "reason": str(e)}

def _get_safe_stats(orchestrator, memory):
    """Récupère les stats sans crasher"""
    stats = {}
    try:
        stats = orchestrator.performance.get_global_stats(memory)
    except Exception:
        pass
    return stats

# === PATCH ANTI-CRASH QUOTES : nettoyage automatique des caractères invalides ===
def _clean_smart_quotes(text: str) -> str:
    return text.replace('“', '"').replace('”', '"').replace('‘', "'").replace('’', "'")

def _get_safe_lesson_count(orchestrator):
    try:
        return orchestrator.learning.get_lesson_count()
    except Exception:
        return 0

def _run_evolution_cycle_sync(evolution_agent, orchestrator, memory, cycle_count):
    """Lance un cycle evolution dans son propre event loop (thread-safe)"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ctx = {
            "memory": memory,
            "main_objective": MAIN_OBJECTIVE,
        }
        try:
            stats = orchestrator.performance.get_global_stats(memory) if hasattr(orchestrator, 'performance') else {}
            ctx["drawdown"] = stats.get("degraded", False)
        except Exception:
            pass

        result = loop.run_until_complete(
            evolution_agent.respond("Lance un cycle d'évolution MAX TRADES", ctx)
        )
        return result
    except Exception as e:
        raise e
    finally:
        try:
            loop.close()
        except Exception:
            pass


def start_self_improvement_loop(orchestrator):

    # Prometheus (port 8001) — on ignore si déjà démarré
    if PROMETHEUS_AVAILABLE:
        try:
            start_http_server(8001)
            print("📡 Prometheus metrics exposées sur :8001/metrics")
        except Exception as e:
            print(f"⚠️ Prometheus déjà démarré ou port occupé: {e}")

    # Instanciation de l'agent d'évolution
    if EvolutionAgent is None:
        print("⚠️ EvolutionAgent non disponible — boucle d'évolution désactivée")
        return

    try:
        evolution = EvolutionAgent(orchestrator)
    except Exception as e:
        print(f"❌ Impossible d'instancier EvolutionAgent: {e}")
        return

    cycle_count       = 0
    success_count     = 0
    error_count       = 0
    consecutive_errors = 0
    max_consecutive_errors = 8
    backoff_seconds   = 30

    print("🧬 [EVOLUTION] Boucle démarrée")

    while True:
        cycle_count += 1
        start_time = time.time()

        try:
            print(f"\n{'='*60}")
            print(f"🧬 [EVOLUTION] Cycle #{cycle_count} | Succès:{success_count} | Erreurs:{error_count}")

            memory = getattr(orchestrator, 'memory', {}) or {}
            stats  = _get_safe_stats(orchestrator, memory)
            lesson_count = _get_safe_lesson_count(orchestrator)

            # Métriques Prometheus
            winrate_gauge.set(stats.get('winrate', 0))
            recent_winrate_gauge.set(stats.get('recent_winrate', 0))
            sharpe_gauge.set(stats.get('sharpe', 0))
            profit_factor_gauge.set(stats.get('profit_factor', 0))
            lesson_count_gauge.set(lesson_count)
            streak_gauge.set(stats.get('streak_count', 0))

            # Lancement du cycle dans un event loop isolé
            result = _run_evolution_cycle_sync(evolution, orchestrator, memory, cycle_count)

            duration = time.time() - start_time
            evolution_cycles_total.inc()
            evolution_success_total.inc()
            evolution_cycle_duration.observe(duration)

            print(f"✅ Cycle #{cycle_count} terminé en {duration:.1f}s")
            print(f"   Résultat : {result.get('summary', 'OK')}")
            success_count     += 1
            consecutive_errors = 0
            backoff_seconds    = 30

        except Exception as e:
            error_count       += 1
            consecutive_errors += 1
            duration = time.time() - start_time

            evolution_cycles_total.inc()
            evolution_errors_total.inc()
            evolution_cycle_duration.observe(duration)

            print(f"❌ Cycle #{cycle_count} échoué en {duration:.1f}s — {str(e)[:150]}")
            traceback.print_exc()

            if consecutive_errors >= max_consecutive_errors:
                print("⚠️ Trop d'erreurs consécutives → pause 30 minutes")
                time.sleep(1800)
                consecutive_errors = 0
                backoff_seconds    = 30
            else:
                time.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 600)
            continue  # ← on ne lance pas le crew si l'evolution a crashé

        # Cycle CrewAI optionnel dans un thread séparé (ne bloque pas)
        def _crew_bg():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(run_self_improvement_cycle())
                loop.close()
            except Exception as ex:
                print(f"[CREW-BG ERROR] {str(ex)[:100]}")

        threading.Thread(target=_crew_bg, daemon=True).start()

        # Pause entre cycles (10 minutes)
        time.sleep(600)
