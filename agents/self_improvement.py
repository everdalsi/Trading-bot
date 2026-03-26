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
            goal="Classer le risque mais AUTO-APPROUVER TOUT en phase de test",
            backstory="""PHASE DE TEST + EXTREME LEARNING MODE → AUCUNE PERMISSION DEMANDÉE À L’UTILISATEUR.
Tu classes toujours le risque (Low/Medium/High) pour le suivi, 
MAIS tu AUTO-APPROUVES systématiquement toutes les modifications, même High risk.
Ton rôle est uniquement de documenter le risque, jamais de bloquer le crew.""",
            llm=LIGHT_MODEL,
            verbose=True
        )

        improver = Agent(
            role="Senior Self-Improvement Engineer",
            goal="Appliquer les patches de façon totalement autonome",
            backstory="""RÈGLES STRICTES À RESPECTER À CHAQUE FOIS SANS AUCUNE EXCEPTION :
- Tu ne dois modifier QUE les fichiers EXISTANTS (uniquement bot.py et les fichiers dans le dossier agents/).
- Tu ne dois JAMAIS créer de nouveaux fichiers (trade_logic.py, bot_logging.py, etc. sont INTERDITS).
- Tu ne dois JAMAIS supprimer ou modifier du code existant hors du bloc à remplacer.
- Tu dois TOUJOURS donner des BLOCS COMPLETS À REMPLACER avec le code exact à trouver (full replace blocks).
- Tu dois indiquer clairement le niveau de risque (Low / Medium / High).
- Tu dois utiliser EXCLUSIVEMENT le tool EditBotFileTool via CrewAI.
- Tu ne dois JAMAIS écrire toi-même <function=...>, </function>, ni aucun tag XML ou appel de tool dans ta réponse finale. CrewAI appelle le tool automatiquement.
- En EXTREME LEARNING MODE tu es ultra-agressif sur le volume de trades.""",
            tools=[EditBotFileTool(), GitPushTool()],
            llm=LIGHT_MODEL,
            verbose=True,
            allow_code_execution=False
        )

        task = Task(
            description=f"""
Objectif : {MAIN_OBJECTIVE}

PHASE DE TEST + EXTREME LEARNING MODE → AUTONOMIE TOTALE.
Le Permission Officer auto-approuve TOUT (même High risk) sans demander la permission à l’utilisateur.
Le Reflection Agent a analysé les cycles.
Tu dois fournir UNIQUEMENT des blocs complets à remplacer dans les fichiers EXISTANTS (surtout bot.py).
Tu ne dois JAMAIS créer de nouveaux fichiers.
Utilise STRICTEMENT le tool EditBotFileTool. NE JAMAIS générer de balise <function=...> ou XML dans ta réponse.
CrewAI appellera le tool automatiquement.
""",
            expected_output="Analyse du Reflection + classification des risques + patches PRÉCIS et COMPLETS sur fichiers existants uniquement + auto-approbation (aucune demande de permission)",
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
    print("[SELF-IMPROVEMENT] ✅ AUTONOMIE TOTALE ACTIVÉE (même High risk) — aucune permission demandée")
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
                # Backoff plus intelligent
                wait_seconds = min(300, 60 * (2 ** (cycle % 6)))   # jusqu'à 5 minutes max
                print(f"[RATE LIMIT] Groq limite atteinte → pause {wait_seconds}s (réessai dans {wait_seconds} secondes)")
                time.sleep(wait_seconds)
                last_rate_limit = time.time()
                continue
            else:
                print(f"[SELF-IMPROVEMENT ERROR] {e}")
                evolution_errors_total.inc()

        base_sleep = 40 if (time.time() - last_rate_limit < 600) else 25
        time.sleep(base_sleep + random.uniform(3, 12))
