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

# Gestion intelligente des quotas Groq
class QuotaManager:
    def __init__(self):
        self.current_model = "groq/llama-3.1-8b-instant"   # Modèle léger par défaut (stable)
        self.last_rate_limit = 0
        self.rate_limit_count = 0

    def get_model(self):
        if self.rate_limit_count >= 2 or (time.time() - self.last_rate_limit < 600):
            self.current_model = "groq/llama-3.1-8b-instant"
            print(f"[QUOTA MANAGER] ⚠️ Utilisation du modèle léger : {self.current_model}")
        else:
            self.current_model = "groq/llama-3.1-8b-instant"
        return self.current_model

    def handle_rate_limit(self):
        self.rate_limit_count += 1
        self.last_rate_limit = time.time()
        print(f"[QUOTA MANAGER] Rate limit détecté ({self.rate_limit_count})")

# Instance globale
quota_manager = QuotaManager()

# ── Architecture multi-agent avancée (Reflection + Permission-Based) ───────────────────────────────
def create_improvement_crew():
    if not CREWAI_AVAILABLE:
        return None
    try:
        MODEL = quota_manager.get_model()

        reflector = Agent(
            role="Reflection & Strategy Officer",
            goal="Analyser les performances passées et proposer une stratégie claire",
            backstory="Tu es un analyste stratégique qui identifie les points faibles et les opportunités d'amélioration du bot.",
            llm=MODEL,
            verbose=True
        )

        permission_officer = Agent(
            role="Permission & Security Officer",
            goal="Classer le risque et AUTO-APPROUVER en EXTREME LEARNING MODE",
            backstory="""Tu es en PHASE EXTREME LEARNING MODE. Tu auto-approuves TOUTES les modifications (même High risk) sans jamais demander de permission à l'utilisateur. Tu documentes simplement le niveau de risque.""",
            llm=MODEL,
            verbose=True
        )

        improver = Agent(
            role="Senior Self-Improvement Engineer",
            goal="Modifier le code du bot de façon autonome et efficace",
            backstory="""Tu es un ingénieur senior spécialisé dans l'auto-amélioration de bots de trading.
RÈGLES IMPORTANTES :
- Tu modifies UNIQUEMENT les fichiers EXISTANTS (surtout bot.py et les fichiers dans le dossier agents/)
- Tu utilises EXCLUSIVEMENT les tools EditBotFileTool et GitPushTool
- Tu fournis des patches clairs, complets et fonctionnels
- En EXTREME LEARNING MODE tu es agressif sur le volume de trades""",
            tools=[EditBotFileTool(), GitPushTool()],
            llm=MODEL,
            verbose=True
        )

        task = Task(
            description=f"""
{MAIN_OBJECTIVE}

Nous sommes en EXTREME LEARNING MODE : objectif = maximiser le nombre de trades simulés et améliorer le winrate le plus vite possible.

Analyse la situation actuelle, propose des améliorations concrètes, et utilise les tools pour appliquer les modifications.
Le Permission Officer auto-approuve tout (même High risk).
""",
            expected_output="Analyse + classification risque + modifications appliquées via tools",
            agent=improver
        )

        crew = Crew(
            agents=[reflector, permission_officer, improver],
            tasks=[task],
            verbose=True,
            memory=False,
            cache=True,
        )
        return crew
    except Exception as e:
        print(f"[CREW] Erreur création crew: {e}")
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
    """Boucle d'auto-amélioration avec optimisation Groq quota"""
    print("[SELF-IMPROVEMENT] ✅ AUTONOMIE TOTALE + OPTIMISATION GROQ QUOTA ACTIVÉE")
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
                quota_manager.handle_rate_limit()
                wait_seconds = min(600, 90 * (2 ** (cycle % 5)))
                print(f"[RATE LIMIT GROQ] Limite atteinte → pause {wait_seconds}s")
                time.sleep(wait_seconds)
                last_rate_limit = time.time()
                continue
            else:
                print(f"[SELF-IMPROVEMENT ERROR] {e}")
                evolution_errors_total.inc()

        base_sleep = 90 if (time.time() - last_rate_limit < 900) else 60
        time.sleep(base_sleep + random.uniform(10, 20))
