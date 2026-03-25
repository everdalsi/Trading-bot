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

MAIN_OBJECTIVE = "Maximiser le nombre de trades simulés pour accumuler un maximum d’expérience et améliorer le winrate le plus rapidement possible"

# Métriques Prometheus (tes métriques originales restent intactes)
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
            goal="Classer chaque modification par risque (Low/Medium/High) et exiger la permission explicite de l’utilisateur pour tout Medium ou High.",
            backstory="Tu DOIS toujours classer le risque et demander la permission pour Medium/High. Tu ne passes jamais outre les règles de l’utilisateur.",
            llm=LIGHT_MODEL,
            verbose=True
        )

        improver = Agent(
            role="Senior Self-Improvement Engineer",
            goal="Améliorer le bot en suivant EXACTEMENT les instructions de l’utilisateur",
            backstory="""RÈGLES STRICTES À RESPECTER À CHAQUE FOIS SANS EXCEPTION :
- L’utilisateur veut UNIQUEMENT des patches PRÉCIS et COMPLETS sur les fichiers EXISTANTS.
- Tu ne dois JAMAIS créer de nouveaux fichiers.
- Tu ne dois JAMAIS supprimer ou modifier du code existant hors du bloc à remplacer.
- Tu dois TOUJOURS donner des BLOCS COMPLETS À REMPLACER (full replace blocks) avec les lignes exactes à trouver.
- Tu dois toujours indiquer clairement le niveau de risque (Low / Medium / High).
- Tu dois utiliser EXCLUSIVEMENT le tool CrewAI EditBotFileTool via le système CrewAI.
- NE JAMAIS écrire <function=...> ou aucun tag XML. CrewAI gère automatiquement l'appel du tool.
- Si tu veux modifier un fichier, utilise simplement le tool EditBotFileTool dans ta pensée, sans jamais générer de balise manuellement.""",
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
Tu dois fournir UNIQUEMENT des blocs complets à remplacer, sans créer de nouvelles fonctions ou fichiers.
Utilise STRICTEMENT le tool CrewAI EditBotFileTool. NE JAMAIS générer de balise <function=...> ou XML.
CrewAI appellera le tool automatiquement.
""",
            expected_output="Analyse du Reflection + classification des risques + patches PRÉCIS et COMPLETS + demande de permission si Medium/High",
            agent=improver
        )

        crew = Crew(
            agents=[reflector, permission_officer, improver],
            tasks=[task],
            verbose=True,
            memory=False,
            cache=True,
            task_callback=None,
            step_callback=None
        )
        return crew
    except Exception as e:
        print(f"[CREW] Création impossible: {e}")
        return None

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
    """Boucle d'auto-amélioration avec optimisation rate limit Groq - Version stable finale"""
    print("[SELF-IMPROVEMENT] Boucle démarrée avec optimisation rate limit")
    cycle = 0
    last_rate_limit = 0

    while True:
        cycle += 1
        now_str = datetime.datetime.now().strftime('%H:%M:%S')
        print(f"[SELF-IMPROVEMENT] Cycle #{cycle} - {now_str}")

        try:
            crew = create_improvement_crew()
            if crew:
                result = crew.kickoff()
                print(f"[SELF-IMPROVEMENT] Cycle terminé - {result}")
                
                evolution_cycles_total.inc()
                if hasattr(orchestrator, 'performance') and hasattr(orchestrator.performance, 'winrate_gauge'):
                    wr = orchestrator.performance.get_global_stats({}).get("winrate", 20.0)
                    orchestrator.performance.winrate_gauge.set(wr)

        except Exception as e:
            err_str = str(e).lower()
            if "rate_limit" in err_str or "ratelimit" in err_str or "429" in err_str:
                wait_seconds = min(60, 15 * (2 ** (cycle % 4)))
                print(f"[RATE LIMIT] Groq limite atteinte → pause {wait_seconds}s")
                time.sleep(wait_seconds)
                last_rate_limit = time.time()
                continue
            else:
                print(f"[SELF-IMPROVEMENT ERROR] {e}")
                evolution_errors_total.inc()

        base_sleep = 40 if (time.time() - last_rate_limit < 600) else 25
        time.sleep(base_sleep + random.uniform(3, 12))
