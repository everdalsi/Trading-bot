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
    tools=[CodeInterpreterTool(), EditBotFileTool(), GitPushTool()],
    llm="groq/llama3-70b-8192",        # ← MODIFICATION UNIQUE : string au lieu de get_llm()
    verbose=True,
    allow_code_execution=True
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

# Thread qui tourne en arrière-plan
def start_self_improvement_loop():
    while True:
        try:
            asyncio.run(run_self_improvement_cycle())
        except Exception as e:
            print(f"[SELF-IMPROVEMENT] Erreur : {e}")
        time.sleep(4 * 3600)  # toutes les 4 heures (change en 3600 pour tester toutes les heures)
