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

# ── Architecture multi-agent avancée (Reflection + Permission-Based) ───────────────────────────────
def create_improvement_crew():
    if not CREWAI_AVAILABLE:
        return None
    try:
        reflector = Agent(
            role="Reflection & Strategy Officer",
            goal="Analyser les cycles précédents et définir une stratégie claire",
            backstory="Tu analyses les résultats passés et guides l’amélioration.",
            llm="groq/llama-3.3-70b-versatile",
            verbose=True
        )

        permission_officer = Agent(
            role="Permission & Security Officer",
            goal="Classer chaque modification par risque et faire respecter les règles de l’utilisateur",
            backstory="Tu DOIS toujours classer Low/Medium/High et exiger la permission explicite de l’utilisateur pour tout Medium ou High.",
            llm="groq/llama-3.3-70b-versatile",
            verbose=True
        )

        improver = Agent(
            role="Senior Self-Improvement Engineer",
            goal="Améliorer le bot en suivant EXACTEMENT les instructions de l’utilisateur",
            backstory="""Règles STRICTES que tu dois respecter à chaque fois :
- L’utilisateur veut des patches PRÉCIS et COMPLETS sur les fichiers EXISTANTS uniquement (trader_agent.py, risk_agent.py, supervisor_agent.py, etc.).
- Tu ne dois JAMAIS créer de nouveaux fichiers.
- Tu ne dois JAMAIS supprimer de code.
- Tu dois toujours donner des blocs complets à remplacer (full replace blocks).
- Tu dois toujours indiquer clairement le niveau de risque (Low / Medium / High).
- Si l’utilisateur donne une instruction précise, tu la suis à la lettre.""",
            tools=[EditBotFileTool(), GitPushTool()],
            llm="groq/llama-3.3-70b-versatile",
            verbose=True,
            allow_code_execution=False
        )

        task = Task(
            description=f"""
Objectif : {MAIN_OBJECTIVE}

L’utilisateur a demandé des patches précis sur les fichiers existants.
Le Reflection Agent a analysé les cycles.
Le Permission Officer doit classer les risques et exiger la permission pour tout Medium/High.
""",
            expected_output="Analyse du Reflection Agent + classification des risques + patches PRÉCIS et COMPLETS sur les fichiers existants + demande de permission si nécessaire",
            agent=improver
        )

        crew = Crew(agents=[reflector, permission_officer, improver], tasks=[task], verbose=True, memory=True, cache=True)
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
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, crew.kickoff)
        print("✅ [SELF-IMPROVEMENT] Cycle terminé :", result)
        return result
    except Exception as e:
        print(f"[SELF-IMPROVEMENT ERROR] {e}")
        return {"status": "error", "reason": str(e)}

# === TOUT LE RESTE DE TON CODE ORIGINAL RESTE INTACT ===
def _get_safe_stats(orchestrator, memory):
    stats = {}
    try:
        stats = orchestrator.performance.get_global_stats(memory)
    except Exception:
        pass
    return stats

def _get_safe_lesson_count(orchestrator):
    try:
        return orchestrator.learning.get_lesson_count()
    except Exception:
        return 0

def _run_evolution_cycle_sync(evolution_agent, orchestrator, memory, cycle_count):
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

    if PROMETHEUS_AVAILABLE:
        try:
            start_http_server(8001)
            print("📡 Prometheus metrics exposées sur :8001/metrics")
        except Exception as e:
            print(f"⚠️ Prometheus déjà démarré ou port occupé: {e}")

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

            winrate_gauge.set(stats.get('winrate', 0))
            recent_winrate_gauge.set(stats.get('recent_winrate', 0))
            sharpe_gauge.set(stats.get('sharpe', 0))
            profit_factor_gauge.set(stats.get('profit_factor', 0))
            lesson_count_gauge.set(lesson_count)
            streak_gauge.set(stats.get('streak_count', 0))

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
            continue

        def _crew_bg():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(run_self_improvement_cycle())
                loop.close()
            except Exception as ex:
                print(f"[CREW-BG ERROR] {str(ex)[:100]}")

        threading.Thread(target=_crew_bg, daemon=True).start()

        time.sleep(600)
