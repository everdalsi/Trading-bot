"""
📦 agents/__init__.py — Registre central V10.1 — 51 agents + ScenarioInjector
"""

from agents.base_agent import BaseAgent, _KnowledgeBaseSingleton
from agents.orchestrator import Orchestrator

# ── Core ──────────────────────────────────────────────────────────────────
from agents.analyst_agent import AnalystAgent
from agents.risk_agent import RiskAgent
from agents.trader_agent import TraderAgent
from agents.supervisor_agent import SupervisorAgent
from agents.quant_ml_agent import QuantMLAgent
from agents.execution_engine_agent import ExecutionEngineAgent
from agents.portfolio_manager import PortfolioManager

# ── Meta ──────────────────────────────────────────────────────────────────
from agents.learning_agent import LearningAgent
from agents.performance_tracker import PerformanceTracker
from agents.research_agent import ResearchAgent
from agents.knowledge_specialist_agent import KnowledgeSpecialistAgent
from agents.evolution_agent import EvolutionAgent
from agents.self_improvement import SelfImprovementAgent
from agents.code_fixer_agent import CodeFixerAgent
from agents.backtest_validator_agent import BacktestValidatorAgent
from agents.soul_agent import SoulAgent

# ── Market ────────────────────────────────────────────────────────────────
from agents.social_listener_agent import SocialListenerAgent
from agents.news_event_agent import NewsEventAgent
from agents.order_book_agent import OrderBookAgent
from agents.funding_rate_agent import FundingRateAgent

# ── Risk ──────────────────────────────────────────────────────────────────
from agents.hedging_agent import HedgingAgent
from agents.drawdown_guard_agent import DrawdownGuardAgent
from agents.correlation_watcher_agent import CorrelationWatcherAgent

# ── Exotic ────────────────────────────────────────────────────────────────
from agents.wallet_copier_agent import WalletCopierAgent
from agents.yield_staking_agent import YieldStakingAgent
from agents.polymarket_arb_agent import PolymarketArbAgent
from agents.event_sniper_agent import EventSniperAgent
from agents.polymarket_trader_agent import PolymarketTraderAgent
from agents.sports_arb_agent import SportsArbAgent

# ── V10 : Nouveaux agents (20) ────────────────────────────────────────────
from agents.quantum_risk_agent import QuantumRiskAgent
from agents.macro_regime_agent import MacroRegimeAgent
from agents.on_chain_agent import OnChainAgent
from agents.derivatives_agent import DerivativesAgent
from agents.liquidation_tracker_agent import LiquidationTrackerAgent
from agents.exchange_flow_agent import ExchangeFlowAgent
from agents.fear_greed_agent import FearGreedAgent
from agents.pattern_recognition_agent import PatternRecognitionAgent
from agents.regime_detector_agent import RegimeDetectorAgent
from agents.arbitrage_scanner_agent import ArbitrageScannerAgent
from agents.macro_calendar_agent import MacroCalendarAgent
from agents.defi_monitor_agent import DefiMonitorAgent
from agents.blockchain_health_agent import BlockchainHealthAgent
from agents.options_flow_agent import OptionsFlowAgent
from agents.cross_asset_agent import CrossAssetAgent
from agents.vol_regime_agent import VolRegimeAgent
from agents.sentiment_aggregator_agent import SentimentAggregatorAgent
from agents.whale_tracker_agent import WhaleTrackerAgent
from agents.regulatory_monitor_agent import RegulatoryMonitorAgent
from agents.grid_strategy_agent import GridStrategyAgent
from agents.token_unlock_agent import TokenUnlockAgent

# ── V10.1 : Scenario Injector (OHMO.AI pre-discovery) ────────────────────────
from agents.scenario_injector_agent import ScenarioInjectorAgent

__all__ = [
    "BaseAgent", "_KnowledgeBaseSingleton", "Orchestrator",
    # Core
    "AnalystAgent", "RiskAgent", "TraderAgent", "SupervisorAgent",
    "QuantMLAgent", "ExecutionEngineAgent", "PortfolioManager",
    # Meta
    "LearningAgent", "PerformanceTracker", "ResearchAgent",
    "KnowledgeSpecialistAgent", "EvolutionAgent", "SelfImprovementAgent",
    "CodeFixerAgent", "BacktestValidatorAgent", "SoulAgent",
    # Market
    "SocialListenerAgent", "NewsEventAgent", "OrderBookAgent", "FundingRateAgent",
    # Risk
    "HedgingAgent", "DrawdownGuardAgent", "CorrelationWatcherAgent",
    # Exotic
    "WalletCopierAgent", "YieldStakingAgent", "PolymarketArbAgent",
    "EventSniperAgent", "PolymarketTraderAgent", "SportsArbAgent",
    # V10
    "QuantumRiskAgent", "MacroRegimeAgent", "OnChainAgent", "DerivativesAgent",
    "LiquidationTrackerAgent", "ExchangeFlowAgent", "FearGreedAgent",
    "PatternRecognitionAgent", "RegimeDetectorAgent", "ArbitrageScannerAgent",
    "MacroCalendarAgent", "DefiMonitorAgent", "BlockchainHealthAgent",
    "OptionsFlowAgent", "CrossAssetAgent", "VolRegimeAgent",
    "SentimentAggregatorAgent", "WhaleTrackerAgent", "RegulatoryMonitorAgent",
    "GridStrategyAgent", "TokenUnlockAgent",
    # V10.1
    "ScenarioInjectorAgent",
]
