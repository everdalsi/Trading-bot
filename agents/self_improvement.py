from crewai import Crew, Task, Agent
from crewai_tools import CodeInterpreterTool
from .tools import EditBotFileTool, GitPushTool
from .config import get_llm, MAIN_OBJECTIVE
from .evolution_agent import EvolutionAgent
import asyncio
import time
import traceback

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
            print(f"🧬 [EVOLUTION] Cycle #{cycle_count} lancé (10 min) | Succès: {success_count} | Erreurs: {error_count}")

            ctx = {
                "memory": getattr(orchestrator, 'memory', {}) if hasattr(orchestrator, 'memory') else {},
                "main_objective": MAIN_OBJECTIVE,
                "drawdown": 0.0,
            }

            result = asyncio.run(evolution.respond("Lance un cycle d'évolution complète", ctx))
            duration = time.time() - start_time

            print(f"✅ [EVOLUTION] Cycle #{cycle_count} terminé en {duration:.1f}s")
            success_count += 1
            consecutive_errors = 0
            backoff_seconds = 30

        except Exception as e:
            error_count += 1
            consecutive_errors += 1
            duration = time.time() - start_time
            print(f"❌ [EVOLUTION] Cycle #{cycle_count} échoué en {duration:.1f}s ({consecutive_errors}/{max_consecutive_errors})")
            print(f"   Erreur: {str(e)[:180]}")
            if traceback.format_exc():
                print(f"   Trace: {traceback.format_exc().splitlines()[-3]}")

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
