"""
📦 agents/__init__.py — Registre central de tous les agents du cerveau collectif
Mise à jour automatique à chaque ajout d'agent.
"""

from agents.base_agent import BaseAgent, _KnowledgeBaseSingleton
from agents.analyst_agent import AnalystAgent
from agents.risk_agent import RiskAgent
from agents.trader_agent import TraderAgent
from agents.orchestrator import Orchestrator
from agents.supervisor_agent import SupervisorAgent
from agents.learning_agent import LearningAgent
from agents.performance_tracker import PerformanceTracker
from agents.research_agent import ResearchAgent
from agents.knowledge_specialist_agent import KnowledgeSpecialistAgent
from agents.evolution_agent import EvolutionAgent
from agents.self_improvement import SelfImprovementAgent
from agents.wallet_copier_agent import WalletCopierAgent
from agents.social_listener_agent import SocialListenerAgent
from agents.quant_ml_agent import QuantMLAgent
from agents.execution_engine_agent import ExecutionEngineAgent
from agents.yield_staking_agent import YieldStakingAgent
from agents.hedging_agent import HedgingAgent
from agents.portfolio_manager import PortfolioManager
from agents.code_fixer_agent import CodeFixerAgent  # NOUVEAU : agent DevOps IA

__all__ = [
    "BaseAgent",
    "_KnowledgeBaseSingleton",
    "AnalystAgent",
    "RiskAgent",
    "TraderAgent",
    "Orchestrator",
    "SupervisorAgent",
    "LearningAgent",
    "PerformanceTracker",
    "ResearchAgent",
    "KnowledgeSpecialistAgent",
    "EvolutionAgent",
    "SelfImprovementAgent",
    "WalletCopierAgent",
    "SocialListenerAgent",
    "QuantMLAgent",
    "ExecutionEngineAgent",
    "YieldStakingAgent",
    "HedgingAgent",
    "PortfolioManager",
    "CodeFixerAgent",
]
