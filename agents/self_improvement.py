import asyncio
import time
import traceback
import threading
import datetime      # ← ajouté
import random        # ← ajouté

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
        # Modèle ultra-rapide et très haut TPM (optimisé rate limit)
        LIGHT_MODEL = "groq/llama-3.1-8b-instant"

        reflector = Agent(
            role="Reflection & Strategy Officer",
            goal="Analyser les cycles précédents et définir une stratégie claire",
            backstory="Tu analyses les résultats passés et guides l’amélioration.",
            llm=LIGHT_MODEL,
            verbose=True
        )

        permission_officer = Agent(
            role="Permission & Security Officer",
            goal="Classer chaque modification par risque et faire respecter les règles de l’utilisateur",
            backstory="Tu DOIS toujours classer Low/Medium/High et exiger la permission explicite de l’utilisateur pour tout Medium ou High.",
            llm=LIGHT_MODEL,
            verbose=True
        )

        improver = Agent(
            role="Senior Self-Improvement Engineer",
            goal="Améliorer le bot en suivant EXACTEMENT les instructions de l’utilisateur",
            backstory="""RÈGLES STRICTES À RESPECTER À CHAQUE FOIS :
- L’utilisateur veut UNIQUEMENT des patches PRÉCIS et COMPLETS sur les fichiers EXISTANTS.
- Tu ne dois JAMAIS créer de nouveaux fichiers.
- Tu ne dois JAMAIS supprimer ou modifier du code existant hors du bloc à remplacer.
- Tu dois toujours donner des BLOCS COMPLETS À REMPLACER (full replace blocks).
- Tu dois toujours indiquer clairement le niveau de risque (Low / Medium / High).""",
            tools=[EditBotFileTool(), GitPushTool()],
            llm=LIGHT_MODEL,
            verbose=True,
            allow_code_execution=False
        )

        task = Task(
            description=f"""
Objectif : {MAIN_OBJECTIVE}

L’utilisateur a demandé des patches précis et complets sur les fichiers existants.
Le Reflection Agent a analysé les cycles.
Le Permission Officer doit classer les risques et exiger la permission pour tout Medium/High.
Tu dois fournir UNIQUEMENT des blocs complets à remplacer.
""",
            expected_output="Analyse + classification risque + patches complets + demande de permission si Medium/High",
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
    """Boucle d'auto-amélioration avec optimisation rate limit Groq"""
    print("[SELF-IMPROVEMENT] Boucle démarrée avec optimisation rate limit")
    cycle = 0
    last_rate_limit = 0

    while True:
        cycle += 1
        print(f"[SELF-IMPROVEMENT] Cycle #{cycle} - {datetime.datetime.now().strftime('%H:%M:%S')}")

        try:
            crew = create_improvement_crew()
            if crew:
                result = crew.kickoff()
                print(f"[SELF-IMPROVEMENT] Cycle terminé - {result}")
                
                # Mise à jour Prometheus
                evolution_cycles_total.inc()
                if hasattr(performance_tracker, 'winrate_gauge'):
                    performance_tracker.winrate_gauge.set(performance_tracker.get_winrate())

        except Exception as e:
            if "rate_limit_exceeded" in str(e).lower() or "RateLimitError" in str(type(e).__name__):
                wait_seconds = min(45, 8 * (2 ** (cycle % 5)))  # backoff exponentiel
                print(f"[RATE LIMIT] Groq limite atteinte → pause {wait_seconds}s")
                time.sleep(wait_seconds)
                last_rate_limit = time.time()
                continue
            else:
                print(f"[SELF-IMPROVEMENT ERROR] {e}")

        # Délai adaptatif optimisé rate limit
        base_sleep = 35 if (time.time() - last_rate_limit < 300) else 22
        time.sleep(base_sleep + random.uniform(0, 8))  # jitter

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
