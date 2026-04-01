"""Orchestrator V10 — 50 parallel trading agents with Bayesian consensus."""

AGENT_PERF_FILE = "/tmp/agent_perf.json"  # Poids dynamiques agents — mis à jour après chaque trade


import asyncio
from typing import Dict, Any, List, Tuple, Optional
import os

from agents.base_agent import BaseAgent, _KnowledgeBaseSingleton
from agents.analyst_agent import AnalystAgent
from agents.risk_agent import RiskAgent
from agents.trader_agent import TraderAgent
from knowledge_base import KnowledgeBase
from agents.portfolio_manager import PortfolioManager
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
from agents.code_fixer_agent import CodeFixerAgent
from agents.news_event_agent import NewsEventAgent
from agents.order_book_agent import OrderBookAgent
from agents.funding_rate_agent import FundingRateAgent
from agents.drawdown_guard_agent import DrawdownGuardAgent
from agents.correlation_watcher_agent import CorrelationWatcherAgent
from agents.backtest_validator_agent import BacktestValidatorAgent
from agents.polymarket_arb_agent import PolymarketArbAgent
from agents.event_sniper_agent import EventSniperAgent
from agents.polymarket_trader_agent import PolymarketTraderAgent
from agents.sports_arb_agent import SportsArbAgent

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
from agents.scenario_injector_agent import ScenarioInjectorAgent

from logging_config import logger

# Timeout par agent (secondes)
AGENT_TIMEOUT      = 12.0
PHASE0_TIMEOUT     = 5.0    # Agents rapides (self_improvement, code_fixer, drawdown_guard)
PHASE0_HTTP_TIMEOUT  = 12.0
PHASE0_CACHE_TTL     = 25.0  # Cache Phase0 (25s TTL)

async def _safe_call(agent: BaseAgent, question: str, context: dict, timeout: float = AGENT_TIMEOUT) -> Dict[str, Any]:
    """
    Appelle un agent avec timeout et gestion d'erreur.
    Retourne un résultat neutre si l'agent dépasse le timeout ou plante.
    """
    try:
        return await asyncio.wait_for(
            agent.safe_respond(question, context),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.warning(f"[ORCH] ⏱ Timeout agent {agent.name} ({timeout}s)")
        return {
            "agent": agent.name, "summary": f"{agent.name}: timeout",
            "confidence": 0.0, "recommendation": "HOLD", "timeout": True
        }
    except Exception as e:
        logger.error(f"[ORCH] ❌ Agent {agent.name} erreur: {e}")
        return {
            "agent": agent.name, "summary": f"{agent.name}: erreur ({e})",
            "confidence": 0.0, "recommendation": "HOLD", "error": str(e)
        }

class Orchestrator:

    def __init__(self):
        # Singleton KB — partagé par TOUS les agents
        self.kb = _KnowledgeBaseSingleton.get_instance()

        self.analyst              = AnalystAgent()
        self.risk                 = RiskAgent()
        self.trader               = TraderAgent()
        self.supervisor           = SupervisorAgent()
        self.learning             = LearningAgent()
        self.performance          = PerformanceTracker()
        self.research             = ResearchAgent()
        self.knowledge_specialist = KnowledgeSpecialistAgent()
        self.evolution            = EvolutionAgent(orchestrator=self)
        self.self_improvement     = SelfImprovementAgent(orchestrator=self)
        self.portfolio_manager    = PortfolioManager()
        self.wallet_copier        = WalletCopierAgent()
        self.social_listener      = SocialListenerAgent()

        self.quant_ml           = QuantMLAgent()
        self.execution_engine   = ExecutionEngineAgent()
        self.yield_staking      = YieldStakingAgent()
        self.hedging            = HedgingAgent()
        self.code_fixer         = CodeFixerAgent()

        self.news_event          = NewsEventAgent()
        self.order_book          = OrderBookAgent()
        self.funding_rate        = FundingRateAgent()
        self.drawdown_guard      = DrawdownGuardAgent()
        self.correlation_watcher = CorrelationWatcherAgent()
        self.backtest_validator  = BacktestValidatorAgent()

        self.polymarket_arb  = PolymarketArbAgent()    # Spread Polymarket vs CEX
        self.event_sniper    = EventSniperAgent()       # Liquidations/OI/funding/volume
        self.polymarket_trader  = PolymarketTraderAgent()    # Direct Polymarket trading
        self.sports_arb         = SportsArbAgent()           # Sports latency arbitrage

        self.quantum_risk          = QuantumRiskAgent()
        self.macro_regime          = MacroRegimeAgent()
        self.on_chain              = OnChainAgent()
        self.derivatives           = DerivativesAgent()
        self.liquidation_tracker   = LiquidationTrackerAgent()
        self.exchange_flow         = ExchangeFlowAgent()
        self.fear_greed            = FearGreedAgent()
        self.pattern_recognition   = PatternRecognitionAgent()
        self.regime_detector       = RegimeDetectorAgent()
        self.arbitrage_scanner     = ArbitrageScannerAgent()
        self.macro_calendar        = MacroCalendarAgent()
        self.defi_monitor          = DefiMonitorAgent()
        self.blockchain_health     = BlockchainHealthAgent()
        self.options_flow          = OptionsFlowAgent()
        self.cross_asset           = CrossAssetAgent()
        self.vol_regime            = VolRegimeAgent()
        self.sentiment_aggregator  = SentimentAggregatorAgent()
        self.whale_tracker         = WhaleTrackerAgent()
        self.regulatory_monitor    = RegulatoryMonitorAgent()
        self.grid_strategy         = GridStrategyAgent()
        self.token_unlock          = TokenUnlockAgent()
        self.scenario_injector     = ScenarioInjectorAgent()   # V10.1 OHMO.AI pre-discovery

        self.debate_rounds = 0
        self._poly_arb_cache   = {}
        self._sniper_cache     = {}
        self._polytrader_cache = {}
        self._sportsarb_cache  = {}
        self._last_news_veto_ts    = 0.0   # Debounce : log VETO News max 1x/60s
        self._last_funding_veto_ts = 0.0   # Debounce : log VETO Funding max 1x/60s
        self._phase0_cache         = {}    # BUG FIX: cache résultat Phase0 (25s TTL)
        self._phase0_cache_ts      = 0.0  # Timestamp dernier cache Phase0
        self._bg_cache          = {}    # Cache résultats background agents
        self._agent_perf        = self._load_agent_perf()  # Poids dynamiques par agent
        self._last_perf_save    = 0.0
        logger.info("[ORCHESTRATOR V10] ✅ 50 agents initialisés — Mode PARALLÈLE + Expansion 30→50 agents")

    def get_backtest_validator(self) -> BacktestValidatorAgent:
        return self.backtest_validator

    def get_drawdown_guard(self) -> DrawdownGuardAgent:
        return self.drawdown_guard

    # PHASE 0 : VÉRIFICATIONS DE SÉCURITÉ (parallèles, veto instantané)

    async def _phase0_security(self, context: dict) -> Tuple[bool, Dict[str, Any]]:
        """
        Vérifications de sécurité en PARALLÈLE.
        Retourne (veto, veto_response) si un veto est déclenché.
        Cache le résultat 25s pour éviter 3-4 appels HTTP par cycle.
        """
        import time as _tp
        _now = _tp.time()
        if _now - self._phase0_cache_ts < PHASE0_CACHE_TTL and self._phase0_cache:
            return self._phase0_cache["veto"], self._phase0_cache["veto_resp"]

        q_immune  = "monitor health"
        q_code    = "code diagnostic health"
        q_guard   = "circuit breaker drawdown guard"
        q_news    = "news event macro critical"
        q_funding = "funding rate check"

        results = await asyncio.gather(
            _safe_call(self.self_improvement,  q_immune,  context, PHASE0_TIMEOUT),      # ≤5s (aucun HTTP)
            _safe_call(self.code_fixer,        q_code,    context, PHASE0_TIMEOUT),      # ≤5s (aucun HTTP)
            _safe_call(self.drawdown_guard,    q_guard,   context, PHASE0_TIMEOUT),      # ≤5s (aucun HTTP)
            _safe_call(self.news_event,        q_news,    context, PHASE0_HTTP_TIMEOUT), # BUG FIX: était 5s, RSS+NewsAPI ~6-10s
            _safe_call(self.funding_rate,      q_funding, context, PHASE0_HTTP_TIMEOUT), # BUG FIX: était 5s, Binance HTTP ~6-8s
            return_exceptions=False
        )

        immune_resp, code_resp, guard_resp, news_resp, funding_resp = results

        _no_veto_resp = {"recommendation": "OK", "confidence": 0.0}
        self._phase0_cache    = {"veto": False, "veto_resp": _no_veto_resp}
        self._phase0_cache_ts = _now

        # Enrichir le contexte
        context["immune_health"] = immune_resp.get("score", 100)
        context["code_health"]   = code_resp.get("health_score", 100)
        context["drawdown_guard"] = guard_resp
        context["news_event"]    = news_resp
        context["funding_rate"]  = funding_resp

        # FIX TRAINING V8: en mode apprentissage, TOUS les vetos sont désactivés
        # Le but est d'accumuler max de trades pour apprendre, pas de se protéger
        import os as _os_tr
        _in_training = _os_tr.environ.get("BOT_TRAINING_MODE", "True").lower() in ("true", "1", "yes")

        def _cache_and_return(veto: bool, resp: dict):
            self._phase0_cache    = {"veto": veto, "veto_resp": resp}
            self._phase0_cache_ts = _now
            return veto, resp

        # DrawdownGuard : veto seulement en LIVE (pas en training)
        if guard_resp.get("veto") and not _in_training:
            logger.warning(f"[ORCH V7] 🛑 VETO DrawdownGuard: {guard_resp.get('veto_reason')}")
            return _cache_and_return(True, {
                "agent": "orchestrator", "summary": guard_resp.get("summary", "Circuit breaker"),
                "confidence": 1.0, "recommendation": "NO TRADE", "veto_source": "drawdown_guard",
            })

        # News : veto sur événement macro critique (désactivé en training)
        if news_resp.get("veto") and not _in_training:
            import time as _time_veto
            _now_veto = _time_veto.time()
            if _now_veto - self._last_news_veto_ts > 60:
                logger.warning(f"[ORCH V7] 📰 VETO News: {news_resp.get('summary', '')[:60]}")
                self._last_news_veto_ts = _now_veto
            return _cache_and_return(True, {
                "agent": "orchestrator", "summary": news_resp.get("summary", "Pause événement macro"),
                "confidence": 1.0, "recommendation": "NO TRADE", "veto_source": "news_event",
                "pause_minutes": news_resp.get("pause_minutes", 0),
            })

        # Funding rate trop élevé (désactivé en training)
        if funding_resp.get("veto") and not _in_training:
            import time as _time_veto2
            _now_veto2 = _time_veto2.time()
            if _now_veto2 - self._last_funding_veto_ts > 60:
                logger.warning(f"[ORCH V7] 💰 VETO FundingRate: {funding_resp.get('summary', '')[:60]}")
                self._last_funding_veto_ts = _now_veto2
            return _cache_and_return(True, {
                "agent": "orchestrator", "summary": funding_resp.get("summary", "Funding rate trop élevé"),
                "confidence": 1.0, "recommendation": "NO TRADE", "veto_source": "funding_rate",
            })

        return False, {}

    # DEMANDE COLLECTIVE PARALLÈLE

    async def ask_all(
        self, question: str, context: dict
    ) -> Tuple[List[Dict], Dict]:
        logger.info(f"[ORCH V10] 🚀 Analyse parallèle 50 agents → {question[:80]}")

        # Glossaire partagé
        context["shared_glossary"] = self.kb.get_glossary()

        # FIX TRAINING V8: propagation du mode training/learning dans le contexte de tous les agents
        import os as _os_orch
        _training_flag = _os_orch.environ.get("BOT_TRAINING_MODE", "True").lower() in ("true", "1", "yes")
        context["training_mode"]         = _training_flag
        context["extreme_learning_mode"] = _training_flag  # active le bypass veto dans supervisor

        # ── Performances historiques agents → calibration de confiance auto ──
        try:
            context["agent_perfs"] = self._load_agent_perf()
        except Exception:
            context["agent_perfs"] = {}

        veto, veto_resp = await self._phase0_security(context)
        if veto:
            return [veto_resp], veto_resp

        # Groupe A : agents indépendants de signal (lancés ensemble)
        group_a = await asyncio.gather(
            _safe_call(self.analyst,              question, context),
            _safe_call(self.quant_ml,             question, context),
            _safe_call(self.order_book,           question, context),
            _safe_call(self.social_listener,      question, context),
            _safe_call(self.research,             question, context),
            _safe_call(self.knowledge_specialist, question, context),
            _safe_call(self.wallet_copier,        question, context),
            _safe_call(self.correlation_watcher,  question, context),
            _safe_call(self.polymarket_arb,       question, context, timeout=8.0),
            _safe_call(self.event_sniper,         question, context, timeout=10.0),
            _safe_call(self.polymarket_trader,  question, context, timeout=10.0),
            _safe_call(self.sports_arb,         question, context, timeout=10.0),
            return_exceptions=False
        )

        (analyst_resp, quant_resp, ob_resp, social_resp,
         research_resp, ks_resp, wc_resp, corr_resp,
         poly_arb_resp, sniper_resp,
         polytrader_resp, sportsarb_resp) = group_a  # FIX: 12 valeurs, 12 variables

        # Enrichir le contexte avec les résultats Phase 1
        context["analysis"]             = analyst_resp.get("analysis", {})
        context["symbol_score"]         = analyst_resp.get("symbol_score", 0.5)
        context["market_regime"]        = quant_resp.get("regime", context.get("market_regime", "NEUTRAL"))
        context["orderbook_imb"]        = ob_resp.get("imbalance", 0.5)
        context["portfolio_correlation"] = corr_resp.get("correlation", 0.0)
        context["size_reduction"]       = corr_resp.get("size_reduction", 1.0)
        context["atr"]                  = analyst_resp.get("analysis", {}).get("tf_1h", {}).get("atr", 0.0)

        # Enrichir contexte V8 : signaux edge
        context["poly_arb_signal"]    = poly_arb_resp.get("signal", "HOLD")
        context["poly_arb_spread"]    = poly_arb_resp.get("best_spread", {}).get("price_gap_pct", 0.0) if poly_arb_resp.get("best_spread") else 0.0
        context["sniper_signal"]      = sniper_resp.get("signal", "HOLD")
        context["sniper_confidence"]  = sniper_resp.get("confidence", 0.0)
        context["liq_pressure_long"]  = sniper_resp.get("liq_long_usd", 0.0)
        context["liq_pressure_short"] = sniper_resp.get("liq_short_usd", 0.0)

        # Cache résultats edge pour le dashboard REST API
        import time as _time
        self._poly_arb_cache = {**poly_arb_resp, "updated_at": int(_time.time())}
        self._sniper_cache   = {**sniper_resp,   "updated_at": int(_time.time())}
        self._polytrader_cache = {**polytrader_resp, "updated_at": int(_time.time())}
        self._sportsarb_cache  = {**sportsarb_resp,  "updated_at": int(_time.time())}

        # Enrichir contexte V9
        context["polytrader_signal"]     = polytrader_resp.get("signal", "HOLD")
        context["polytrader_edge"]       = polytrader_resp.get("avg_edge_pct", 0.0)
        context["polytrader_opps"]       = polytrader_resp.get("markets_with_edge", 0)
        context["sportsarb_signal"]      = sportsarb_resp.get("signal", "HOLD")
        context["sportsarb_total"]       = sportsarb_resp.get("total_found", 0)
        _sarb_opps = sportsarb_resp.get("opportunities", [])
        context["sportsarb_best_profit"] = _sarb_opps[0].get("profit_pct", 0.0) if _sarb_opps else 0.0
        if polytrader_resp.get("signal", "HOLD") != "HOLD":
            logger.info(f"[ORCH V9] 🎯 PolyTrader: {polytrader_resp.get('signal','HOLD')} edge={context['polytrader_edge']:.1f}%")
        if sportsarb_resp.get("signal", "HOLD") != "HOLD":
            logger.info(f"[ORCH V9] ⚡ SportsArb: {sportsarb_resp.get('signal','HOLD')} profit={context['sportsarb_best_profit']:.2f}%")

        # Log si edge actif
        if poly_arb_resp.get("signal", "HOLD") != "HOLD":
            logger.info(f"[ORCH V8] 🏦 PolyArb signal: {poly_arb_resp.get('signal','HOLD')} spread={context['poly_arb_spread']:.2f}%")
        if sniper_resp.get("should_emit"):
            logger.info(f"[ORCH V8] 🎯 Sniper signal: {sniper_resp.get('signal','HOLD')} conf={sniper_resp.get('confidence',0):.0%}")

        group_v10 = await asyncio.gather(
            _safe_call(self.macro_regime,         question, context, timeout=8.0),
            _safe_call(self.on_chain,             question, context, timeout=8.0),
            _safe_call(self.derivatives,          question, context, timeout=8.0),
            _safe_call(self.liquidation_tracker,  question, context, timeout=6.0),
            _safe_call(self.exchange_flow,        question, context, timeout=8.0),
            _safe_call(self.fear_greed,           question, context, timeout=6.0),
            _safe_call(self.pattern_recognition,  question, context, timeout=8.0),
            _safe_call(self.regime_detector,      question, context, timeout=8.0),
            _safe_call(self.arbitrage_scanner,    question, context, timeout=8.0),
            _safe_call(self.macro_calendar,       question, context, timeout=8.0),
            _safe_call(self.defi_monitor,         question, context, timeout=8.0),
            _safe_call(self.blockchain_health,    question, context, timeout=8.0),
            _safe_call(self.options_flow,         question, context, timeout=8.0),
            _safe_call(self.cross_asset,          question, context, timeout=8.0),
            _safe_call(self.vol_regime,           question, context, timeout=8.0),
            _safe_call(self.sentiment_aggregator, question, context, timeout=8.0),
            _safe_call(self.whale_tracker,        question, context, timeout=6.0),
            _safe_call(self.regulatory_monitor,   question, context, timeout=8.0),
            _safe_call(self.grid_strategy,        question, context, timeout=8.0),
            _safe_call(self.token_unlock,         question, context, timeout=6.0),
            _safe_call(self.quantum_risk,         question, context, timeout=8.0),
            _safe_call(self.scenario_injector,    question, context, timeout=10.0),
            return_exceptions=False
        )

        (macro_regime_resp, on_chain_resp, derivatives_resp, liq_resp,
         exflow_resp, fg_resp, pattern_resp, regime_resp,
         arb_resp, macrocal_resp, defi_resp, btchealth_resp,
         opts_resp, crossasset_resp, vol_resp, sentiment_resp,
         whale_resp, reg_resp, grid_resp, unlock_resp,
         quantum_resp, scenario_resp) = group_v10

        # Enrichir le contexte avec les signaux V10
        context["fear_greed_value"]     = fg_resp.get("fg_value", 50)
        context["vol_regime"]           = vol_resp.get("metrics", {}).get("regime", "MEDIUM VOL")
        context["market_regime_adx"]    = regime_resp.get("regime", "TRANSITIONAL")
        context["macro_bias"]           = macro_regime_resp.get("bias", "NEUTRAL")
        context["whale_signal"]         = whale_resp.get("recommendation", "HOLD")
        context["regulatory_alert"]     = reg_resp.get("alert_level", "LOW")
        context["arb_opportunities"]    = arb_resp.get("opportunities", [])
        context["active_patterns"]      = pattern_resp.get("patterns", [])
        context["liq_cascade_score"]    = liq_resp.get("liq_score", 0.5)
        context["quantum_threat"]       = quantum_resp.get("threat_level", 0.0)
        context["scenario_opportunities"] = scenario_resp.get("opportunities", [])
        context["scenario_signals"]       = scenario_resp.get("scenarios_with_edge", 0)
        context["grid_params"]          = grid_resp.get("grid_params", {})
        context["sentiment_score"]      = sentiment_resp.get("sentiment_score", 0.5)

        # Alerte réglementaire haute → flag dans contexte
        if reg_resp.get("alert_level") == "HIGH" and reg_resp.get("reg_score", 0.5) < 0.25:
            logger.warning("[ORCH V10] ⚠️ ALERTE RÉGLEMENTAIRE → réduction exposition")
            context["regulatory_veto"] = True

        logger.info(
            f"[ORCH V10] 📊 V10: F&G={context['fear_greed_value']} | "
            f"Regime={context['market_regime_adx']} | "
            f"Macro={context['macro_bias']} | Whales={context['whale_signal']}"
        )

        group_b = await asyncio.gather(
            _safe_call(self.risk,     question, context),
            _safe_call(self.hedging,  question, context),
            return_exceptions=False
        )
        risk_resp, hedging_resp = group_b

        context["risk"]             = risk_resp
        context["risk_level"]       = risk_resp.get("risk_level", "MODERATE")
        context["kelly_adjusted"]   = risk_resp.get("kelly_adjusted", 0.05)

        # Veto risk CRITICAL — désactivé en training pour maximiser les trades d'apprentissage
        import os as _os_risk
        _in_training_risk = _os_risk.environ.get("BOT_TRAINING_MODE", "True").lower() in ("true", "1", "yes")
        if risk_resp.get("risk_level") in ("CRITICAL",) and not _in_training_risk:
            logger.warning(f"[ORCH V7] ⚠️ VETO Risk CRITICAL (LIVE only)")
            return [risk_resp], {
                "agent": "orchestrator", "summary": risk_resp.get("summary", "Risque critique"),
                "confidence": 1.0, "recommendation": "NO TRADE", "veto_source": "risk",
            }

        trader_resp = await _safe_call(self.trader, question, context)
        context["trader_decision"] = trader_resp  # FIX: supervisor accède context["trader_decision"]

        all_outputs = [
              # V1-V9 agents
              analyst_resp, quant_resp, ob_resp, social_resp,
              research_resp, ks_resp, wc_resp, corr_resp,
              risk_resp, hedging_resp, trader_resp,
              poly_arb_resp, sniper_resp,
              polytrader_resp, sportsarb_resp,
              # V10 — 21 nouveaux agents actifs
              macro_regime_resp, on_chain_resp, derivatives_resp, liq_resp,
              exflow_resp, fg_resp, pattern_resp, regime_resp,
              arb_resp, macrocal_resp, defi_resp, btchealth_resp,
              opts_resp, crossasset_resp, vol_resp, sentiment_resp,
              whale_resp, reg_resp, grid_resp, unlock_resp, quantum_resp,
            scenario_resp,
          ]
        # Filtrer les réponses vides
        all_outputs = [r for r in all_outputs if r and isinstance(r, dict)]

        context["agent_outputs"]    = all_outputs
        context["global_score"]     = self._compute_global_score(all_outputs)
        context["final_confidence"] = context["global_score"]
        context["debate_rounds"]    = self.debate_rounds

        final = await _safe_call(self.supervisor, question, context)

        self.debate_rounds += 1
        logger.info(
            f"[ORCH V10] ✅ Décision: {final.get('decision', final.get('recommendation', '?'))} | "
            f"conf: {final.get('confidence', 0):.0%} | "
            f"{len(all_outputs)} agents | kelly={context.get('kelly_adjusted', 0):.1%} | regime={context.get('market_regime', '?')}"
        )
        return all_outputs, final

    # ANALYSE MULTI-SYMBOLES EN PARALLÈLE

    async def analyze_symbols_parallel(
        self, symbols: List[str], base_context: dict
    ) -> Dict[str, Dict]:
        """
        Lance l'analyse complète sur plusieurs symboles SIMULTANÉMENT.
        Retourne un dict {symbol: (outputs, final_decision)}.
        """
        async def analyze_one(symbol: str) -> Tuple[str, List[Dict], Dict]:
            ctx = dict(base_context)
            ctx["symbol"] = symbol
            try:
                outputs, final = await self.ask_all(f"analyse trading signal {symbol}", ctx)
                return symbol, outputs, final
            except Exception as e:
                logger.error(f"[ORCH V7] Erreur analyse {symbol}: {e}")
                return symbol, [], {"recommendation": "HOLD", "confidence": 0.0}

        tasks = [analyze_one(s) for s in symbols]
        results_raw = await asyncio.gather(*tasks, return_exceptions=False)

        results: Dict[str, Dict] = {}
        for symbol, outputs, final in results_raw:
            results[symbol] = {"outputs": outputs, "final": final}
            logger.info(
                f"[ORCH V7] {symbol}: {final.get('decision', final.get('recommendation', '?'))} "
                f"({final.get('confidence', 0):.0%})"
            )
        return results

    # HELPERS

    # ─── POIDS DYNAMIQUES AGENTS ────────────────────────────────────────────────

    def _load_agent_perf(self) -> dict:
        """Charge le fichier de performance des agents (JSON persisté)."""
        import json as _json, os as _os
        try:
            if _os.path.exists(AGENT_PERF_FILE):
                with open(AGENT_PERF_FILE, "r") as _f:
                    return _json.load(_f)
        except Exception: pass
        return {}

    def _save_agent_perf(self):
        """Sauvegarde le fichier de performance des agents."""
        import json as _json
        try:
            with open(AGENT_PERF_FILE, "w") as _f:
                _json.dump(self._agent_perf, _f)
        except Exception: pass

    def _get_perf_multiplier(self, agent_name: str) -> float:
        """
        Retourne un multiplicateur [0.5, 2.0] basé sur la précision historique de l'agent.
        Un agent correct 80% du temps → 1.5x | 30% → 0.6x | nouveau → 1.0x
        """
        perf = self._agent_perf.get(agent_name, {})
        hits = perf.get("hits", 0)
        total = perf.get("total", 0)
        if total < 10:
            return 1.0  # Pas assez de données → poids nominal
        accuracy = hits / total
        # Mapping: 60%+ → boost, <40% → malus
        if accuracy >= 0.75:  return 2.0
        if accuracy >= 0.65:  return 1.5
        if accuracy >= 0.55:  return 1.2
        if accuracy >= 0.45:  return 1.0
        if accuracy >= 0.35:  return 0.7
        return 0.5  # Agent peu fiable

    def update_agent_outcome(self, all_outputs: list, trade_won: bool):
        """
        Appelé après clôture d'un trade. Met à jour la précision de chaque agent.
        Un agent est "correct" si sa recommandation est alignée avec le résultat.
        """
        import time as _time
        changed = False
        for out in all_outputs:
            if not isinstance(out, dict): continue
            name = out.get("agent", "")
            if not name: continue
            reco = str(out.get("recommendation", out.get("decision", "HOLD"))).upper()
            is_buy  = any(x in reco for x in ["BUY", "LONG", "BULLISH"])
            is_sell = any(x in reco for x in ["SELL", "SHORT", "BEARISH"])
            is_hold = not (is_buy or is_sell)
            if is_hold: continue  # HOLD ne contribue pas aux stats
            # L'agent est correct si : (il dit BUY et le trade est gagnant) OU (il dit SELL et trade perdant)
            correct = (is_buy and trade_won) or (is_sell and not trade_won)
            if name not in self._agent_perf:
                self._agent_perf[name] = {"hits": 0, "total": 0, "accuracy": 0.5}
            self._agent_perf[name]["total"] += 1
            if correct:
                self._agent_perf[name]["hits"] += 1
            self._agent_perf[name]["accuracy"] = round(
                self._agent_perf[name]["hits"] / self._agent_perf[name]["total"], 4
            )
            changed = True
        if changed:
            # Sauvegarder périodiquement (pas à chaque trade)
            import time as _t2
            if _t2.time() - self._last_perf_save > 60:
                self._last_perf_save = _t2.time()
                self._save_agent_perf()
                logger.info(f"[ORCH] 📊 Agent perf updated: {len(self._agent_perf)} agents trackés")

    # ═══════════════════════════════════════════════════════════════════
    # BACKGROUND AGENTS — travail pendant HOLD (Option C: analyse+learn)
    # ═══════════════════════════════════════════════════════════════════

    async def run_background_agents(self, ctx: dict, cycle_id: int) -> dict:
        """
        Lance tous les agents en tâche de fond pendant les HOLD.
        Cycle pair  → pré-analyse technique (non-LLM, instantané)
        Cycle impair → auto-amélioration (révise perf, ajuste confiance)
        Résultats stockés dans self._bg_cache pour le prochain débat.
        """
        import asyncio as _asyncio, time as _bgtime
        _t0 = _bgtime.time()
        mode = "preanalysis" if cycle_id % 2 == 0 else "self_improve"
        logger.info(f"[BG-AGENTS] 🔄 Cycle {cycle_id} → mode: {mode} | {50} agents en arrière-plan")

        # Collecter tous les agents dans une liste
        _all_agents = [
            self.analyst, self.quant_ml, self.order_book, self.social_listener,
            self.research, self.knowledge_specialist, self.wallet_copier,
            self.correlation_watcher, self.polymarket_arb, self.event_sniper,
            self.polymarket_trader, self.sports_arb, self.quantum_risk,
            self.macro_regime, self.on_chain, self.derivatives,
            self.liquidation_tracker, self.exchange_flow, self.fear_greed,
            self.pattern_recognition, self.regime_detector, self.arbitrage_scanner,
            self.macro_calendar, self.defi_monitor, self.blockchain_health,
            self.options_flow, self.cross_asset, self.vol_regime,
            self.sentiment_aggregator, self.whale_tracker, self.regulatory_monitor,
            self.grid_strategy, self.token_unlock, self.scenario_injector,
            self.risk, self.trader, self.supervisor, self.learning,
            self.portfolio_manager, self.self_improvement, self.evolution,
            self.code_fixer, self.news_event, self.funding_rate,
            self.drawdown_guard, self.yield_staking, self.hedging,
            self.backtest_validator, self.performance,
        ]

        # Lancer bg_tick() sur chaque agent — non-bloquant, timeout 2s
        async def _safe_bg(agent):
            try:
                loop = _asyncio.get_event_loop()
                result = await _asyncio.wait_for(
                    loop.run_in_executor(None, agent.bg_tick, ctx, cycle_id),
                    timeout=2.0
                )
                return result
            except Exception:
                return {"type": "skip", "agent": getattr(agent, "name", "?")}

        results = await _asyncio.gather(*[_safe_bg(a) for a in _all_agents],
                                        return_exceptions=True)

        # Agréger les pré-signaux (pour injecter dans le prochain débat)
        pre_signals = [r for r in results
                       if isinstance(r, dict) and r.get("type") == "preanalysis"
                       and r.get("signal", {}).get("signal") in ("BUY", "SELL")]
        buy_count  = sum(1 for s in pre_signals if s.get("signal", {}).get("signal") == "BUY")
        sell_count = sum(1 for s in pre_signals if s.get("signal", {}).get("signal") == "SELL")

        _elapsed = round(_bgtime.time() - _t0, 2)
        summary = {
            "cycle_id":   cycle_id, "mode": mode, "elapsed_s": _elapsed,
            "agents_ran": len([r for r in results if isinstance(r, dict)]),
            "pre_buy":    buy_count, "pre_sell": sell_count,
            "pre_bias":   "BUY" if buy_count > sell_count * 1.4 else
                          "SELL" if sell_count > buy_count * 1.4 else "NEUTRAL",
        }
        self._bg_cache = summary
        logger.info(f"[BG-AGENTS] ✅ {mode} terminé {_elapsed}s | BUY:{buy_count} SELL:{sell_count} → bias:{summary['pre_bias']}")
        return summary

    async def scan_pump_setups(self, symbols: list, prices: dict, closes_map: dict) -> list:
        """
        Détecte les setups pump rapides (style $NOTHING) sur tous les symboles.
        Critères : prix +3% en <5 candles AND volume spike AND RSI > 60.
        Retourne liste d'alertes pump triées par force décroissante.
        """
        import time as _pt
        alerts = []
        for sym in symbols:
            try:
                closes = closes_map.get(sym, [])
                if len(closes) < 20: continue
                # Variation récente sur 5 candles
                price_now = closes[-1]
                price_5c  = closes[-6] if len(closes) >= 6 else closes[0]
                if price_5c <= 0: continue
                pct_5c = (price_now - price_5c) / price_5c * 100
                # RSI sur 14 périodes
                rsi = self.pattern_recognition.tools.rsi(closes, 14)
                # EMA 9 vs 21 pour momentum
                ema9  = self.pattern_recognition.tools.ema(closes, 9)
                ema21 = self.pattern_recognition.tools.ema(closes, 21)
                ema_cross = ema9 > ema21  # momentum haussier
                # Détecter pump : +3% en 5 candles + RSI > 60 + EMA cross
                if pct_5c >= 3.0 and rsi > 60 and ema_cross:
                    strength = round(pct_5c * (rsi / 50), 2)
                    alerts.append({
                        "symbol": sym, "type": "PUMP",
                        "pct_5c": round(pct_5c, 2), "rsi": round(rsi, 1),
                        "strength": strength, "ts": int(_pt.time()),
                        "action": "BUY",
                        "reason": f"Pump {pct_5c:+.1f}% / RSI {rsi:.0f} / EMA↑"
                    })
                # Détecter dump (position short opportuniste)
                elif pct_5c <= -3.0 and rsi < 40 and not ema_cross:
                    strength = round(abs(pct_5c) * ((100 - rsi) / 50), 2)
                    alerts.append({
                        "symbol": sym, "type": "DUMP",
                        "pct_5c": round(pct_5c, 2), "rsi": round(rsi, 1),
                        "strength": strength, "ts": int(_pt.time()),
                        "action": "SELL",
                        "reason": f"Dump {pct_5c:+.1f}% / RSI {rsi:.0f} / EMA↓"
                    })
            except Exception:
                continue
        alerts.sort(key=lambda x: x["strength"], reverse=True)
        if alerts:
            logger.info(f"[PUMP-SCAN] 🚀 {len(alerts)} setups détectés → top: {alerts[0]['symbol']} {alerts[0]['type']} {alerts[0]['pct_5c']:+.1f}%")
        return alerts[:5]  # Top 5 setups max

    def _compute_global_score(self, outputs: List[Dict]) -> float:
        """Score global pondéré sur tous les agents."""
        WEIGHTS = {
            # Agents core
            "analyst":              0.18, "risk":                0.16,
            "quant_ml":             0.11, "order_book":          0.09,
            "trader":               0.13, "research":            0.07,
            "social_listener":      0.04, "hedging":             0.04,
            "knowledge_specialist": 0.03, "wallet_copier":       0.02,
            # BUG FIX: Agents V8/V9 manquants (ils avaient le fallback 0.02)
            "polymarket_trader":    0.07, # edge élevé → poids significatif
            "polymarket_arb":       0.05,
            "event_sniper":         0.05,
            "sports_arb":           0.04, # arb garanti → signal fiable
            # Agents V10 — expansion
            "quantum_risk":        0.05, "macro_regime":        0.04,
            "on_chain":            0.04, "derivatives":         0.04,
            "fear_greed":          0.03, "whale_tracker":       0.03,
            "sentiment_aggregator":0.03, "regulatory_monitor":  0.04,
            "regime_detector":     0.03, "pattern_recognition": 0.03,
            "vol_regime":          0.02, "options_flow":        0.03,
            "liquidation_tracker": 0.02, "arbitrage_scanner":   0.03,
            "cross_asset":         0.02, "exchange_flow":       0.02,
            "macro_calendar":      0.02, "defi_monitor":        0.02,
            "blockchain_health":   0.01, "token_unlock":        0.01,
            "grid_strategy":       0.02,
            "scenario_injector":   0.03,
        }
        total_w, weighted_sum = 0.0, 0.0
        for out in outputs:
            name = out.get("agent", "")
            conf = float(out.get("confidence", 0.5))
            reco = str(out.get("recommendation", out.get("decision", "HOLD"))).upper()
            w = WEIGHTS.get(name, 0.02) * self._get_perf_multiplier(name)
            # Convertir recommandation en score [0, 1]
            if any(x in reco for x in ["BUY", "LONG", "BULLISH"]):
                score = 0.5 + conf * 0.5
            elif any(x in reco for x in ["SELL", "SHORT", "BEARISH"]):
                score = 0.5 - conf * 0.5
            else:
                score = 0.5
            weighted_sum += w * score
            total_w += w
        return round(weighted_sum / total_w, 4) if total_w > 0 else 0.5
