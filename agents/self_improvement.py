from crewai import Crew, Task, Agent
from crewai_tools import CodeInterpreterTool
from .tools import EditBotFileTool, GitPushTool
from .config import get_llm, MAIN_OBJECTIVE
from .evolution_agent import EvolutionAgent
import asyncio
import time
import traceback
from prometheus_client import start_http_server, Counter, Gauge, Histogram

# ==================== PROMETHEUS METRICS ====================
evolution_cycles_total = Counter('evolution_cycles_total', 'Nombre total de cycles d\'évolution')
evolution_success_total = Counter('evolution_success_total', 'Cycles d\'évolution réussis')
evolution_errors_total = Counter('evolution_errors_total', 'Cycles d\'évolution en erreur')
evolution_cycle_duration = Histogram('evolution_cycle_duration_seconds', 'Durée d\'un cycle d\'évolution', buckets=[5, 10, 20, 30, 60, 120])
winrate_gauge = Gauge('bot_winrate_percent', 'Winrate actuel du bot')
recent_winrate_gauge = Gauge('bot_recent_winrate_percent', 'Winrate des 20 derniers trades')
sharpe_gauge = Gauge('bot_sharpe_ratio', 'Sharpe ratio actuel')
profit_factor_gauge = Gauge('bot_profit_factor', 'Profit Factor')
lesson_count_gauge = Gauge('bot_lesson_count', 'Nombre de leçons en base')
streak_gauge = Gauge('bot_current_streak', 'Longueur de la streak actuelle')

improver = Agent(
    role="Senior Self-Improvement Engineer",
    goal="Améliorer constamment le bot et les agents à partir de l'objectif principal",
    backstory="Tu es un ingénieur IA autonome. Tu analyses les performances, proposes du code, le testes et déploies.",
    tools=[EditBotFileTool(), GitPushTool()],
    llm="groq/llama3-70b-8192",
    verbose=True,
    allow_code_execution=False
)

def create_improvement_crew():
    task = Task(
        description=f"""
        Objectif principal du bot : {MAIN_OBJECTIVE}
        
        Analyse les stats actuelles (PerformanceTracker + LearningAgent DB), 
        identifie les faiblesses (drawdown, streak de pertes, WR, etc.),
        propose des améliorations concrètes (nouveau code, réglage de paramètres, nouvelle règle),
        écris le code, teste-le avec CodeInterpreter, puis push sur GitHub si c'est valide.
        """,
        expected_output="Code modifié + message de commit + décision de déploiement",
        agent=improver
    )

    crew = Crew(
        agents=[improver],
        tasks=[task],
        verbose=2,
        memory=True,
        cache=True
    )
    return crew

async def run_self_improvement_cycle():
    print("🚀 [SELF-IMPROVEMENT] Lancement du cycle d'auto-amélioration...")
    crew = create_improvement_crew()
    result = crew.kickoff()
    print("✅ [SELF-IMPROVEMENT] Cycle terminé :", result)
    return result

def start_self_improvement_loop(orchestrator):
    # Démarrage du serveur Prometheus sur le port 8001 (séparé du serveur principal 8000)
    start_http_server(8001)
    print("📡 Prometheus metrics exposées sur http://ton-app.railway.app:8001/metrics")

    evolution = EvolutionAgent(orchestrator)
    cycle_count = 0
    success_count = 0
    error_count = 0
    consecutive_errors = 0
    max_consecutive_errors = 8
    backoff_seconds = 30

    while True:
        cycle_count += 1
        start_time = time.time()

        try:
            print(f"\n{'='*70}")
            print(f"🧬 [EVOLUTION] Cycle #{cycle_count} | Succès: {success_count}/{cycle_count} | Erreurs: {error_count}")

            # Récupération des métriques réelles
            memory = getattr(orchestrator, 'memory', {}) if hasattr(orchestrator, 'memory') else {}
            stats = orchestrator.performance.get_global_stats(memory) if hasattr(orchestrator, 'performance') else {}
            lesson_count = orchestrator.learning.get_lesson_count() if hasattr(orchestrator, 'learning') else 0

            # Mise à jour Prometheus
            winrate_gauge.set(stats.get('winrate', 0))
            recent_winrate_gauge.set(stats.get('recent_winrate', 0))
            sharpe_gauge.set(stats.get('sharpe', 0))
            profit_factor_gauge.set(stats.get('profit_factor', 0))
            lesson_count_gauge.set(lesson_count)
            streak_gauge.set(stats.get('streak_count', 0))

            ctx = {
                "memory": memory,
                "main_objective": MAIN_OBJECTIVE,
                "drawdown": stats.get("degraded", False),
            }

            result = asyncio.run(evolution.respond("Lance un cycle d'évolution complète", ctx))
            duration = time.time() - start_time

            evolution_cycles_total.inc()
            evolution_success_total.inc()
            evolution_cycle_duration.observe(duration)

            print(f"✅ Cycle #{cycle_count} terminé en {duration:.1f}s")
            print(f"   Résultat : {result.get('summary', 'OK')}")
            success_count += 1
            consecutive_errors = 0
            backoff_seconds = 30

        except Exception as e:
            error_count += 1
            consecutive_errors += 1
            duration = time.time() - start_time
            evolution_cycles_total.inc()
            evolution_errors_total.inc()
            evolution_cycle_duration.observe(duration)

            print(f"❌ Cycle #{cycle_count} échoué en {duration:.1f}s")
            print(f"   Erreur: {str(e)[:180]}")

            if consecutive_errors >= max_consecutive_errors:
                print("⚠️ Trop d'erreurs consécutives → pause de 30 minutes")
                time.sleep(1800)
                consecutive_errors = 0
                backoff_seconds = 30
            else:
                time.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 600)

        try:
            asyncio.run(run_self_improvement_cycle())
        except Exception as e:
            print(f"[SELF-IMPROVEMENT ERROR] {str(e)[:150]}")

        time.sleep(600)
