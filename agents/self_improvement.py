from crewai import Crew, Task, Agent
from crewai_tools import CodeInterpreterTool
from .tools import EditBotFileTool, GitPushTool
from .config import get_llm, MAIN_OBJECTIVE  # ← tu vas créer ce config.py juste après
import asyncio
import time

# Agent spécialisé auto-codage
improver = Agent(
    role="Senior Self-Improvement Engineer",
    goal="Améliorer constamment le bot et les agents à partir de l'objectif principal",
    backstory="Tu es un ingénieur IA autonome. Tu analyses les performances, proposes du code, le testes et déploies.",
    tools=[EditBotFileTool(), GitPushTool()],   # ← CodeInterpreterTool retiré (ne marche pas sans Docker)
    llm="groq/llama3-70b-8192",
    verbose=True,
    allow_code_execution=False                 # ← MODIFICATION UNIQUE : désactivé pour Railway
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
    """Cycle d'auto-amélioration (à lancer toutes les X heures)"""
    print("🚀 [SELF-IMPROVEMENT] Lancement du cycle d'auto-amélioration...")
    crew = create_improvement_crew()
    result = crew.kickoff()
    print("✅ [SELF-IMPROVEMENT] Cycle terminé :", result)
    return result

## Thread qui tourne en arrière-plan — VERSION TEST ULTRA-RAPIDE
def start_self_improvement_loop(orchestrator):
    """
    Mode TEST : cycle toutes les 10 minutes pour que tu voies l'auto-codage en live
    (change en 3*3600 une fois que tu as testé)
    """
    evolution = EvolutionAgent(orchestrator)

    while True:
        try:
            print("🧬 [EVOLUTION] Cycle d'auto-évolution lancé (MODE TEST - 10 minutes)...")
            ctx = {
                "memory": {},
                "main_objective": MAIN_OBJECTIVE,
                "drawdown": 0.0,
            }
            result = asyncio.run(evolution.respond("Lance un cycle d'évolution complète", ctx))
            print("✅ [EVOLUTION] Cycle terminé :", result.get("summary", "OK"))
        except Exception as e:
            print(f"[EVOLUTION ERROR] {e}")

        # On garde aussi l’ancien cycle CrewAI
        try:
            asyncio.run(run_self_improvement_cycle())
        except Exception as e:
            print(f"[SELF-IMPROVEMENT ERROR] {e}")

        time.sleep(600)   # ← 600 secondes = 10 minutes (idéal pour tester)
