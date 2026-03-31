"""
🎯 ORCHESTRATOR V6.0 — Cerveau Collectif + 6 Nouveaux Agents de Sécurité
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NOUVEAUTÉS V6.0 :
- NewsEventAgent       : Veto trading 30min avant/après événements macro (Fed, CPI, ETF, hack)
- OrderBookAgent       : Signal BUY/SELL basé sur bid/ask imbalance Binance temps réel
- FundingRateAgent     : Veto LONG si funding rate > +0.05% (perp futures)
- DrawdownGuardAgent   : Circuit breaker indépendant (3/5/8 pertes → pause/stop total)
- CorrelationWatcher   : Réduction taille si portefeuille trop corrélé (> 0.80)
- BacktestValidator    : Validation stratégie 7j avant tout git push EvolutionAgent
- Phase 0 étendue : veto précoce si news critique ou circuit breaker actif
"""

import asyncio
from typing import Dict, Any, List, Tuple
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

# ── Nouveaux agents V6.0 ──────────────────────────────────────────────────
from agents.news_event_agent import NewsEventAgent
from agents.order_book_agent import OrderBookAgent
from agents.funding_rate_agent import FundingRateAgent
from agents.drawdown_guard_agent import DrawdownGuardAgent
from agents.correlation_watcher_agent import CorrelationWatcherAgent
from agents.backtest_validator_agent import BacktestValidatorAgent

from logging_config import logger


class Orchestrator:

    def __init__(self):
        # Singleton KB — partagé par TOUS les agents
        self.kb = _KnowledgeBaseSingleton.get_instance()

        # ── Agents cœur ──────────────────────────────────────────────────
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

        # ── Agents spécialisés V5 ────────────────────────────────────────
        self.quant_ml           = QuantMLAgent()
        self.execution_engine   = ExecutionEngineAgent()
        self.yield_staking      = YieldStakingAgent()
        self.hedging            = HedgingAgent()
        self.code_fixer         = CodeFixerAgent()

        # ── Nouveaux agents V6.0 — Sécurité & Signal ─────────────────────
        self.news_event          = NewsEventAgent()
        self.order_book          = OrderBookAgent()
        self.funding_rate        = FundingRateAgent()
        self.drawdown_guard      = DrawdownGuardAgent()
        self.correlation_watcher = CorrelationWatcherAgent()
        self.backtest_validator  = BacktestValidatorAgent()

        self.debate_rounds = 0
        logger.info("[ORCHESTRATOR V6.0] Tous les agents initialisés ✅ (+6 agents sécurité)")

    # ── Expose backtest_validator pour EvolutionAgent ─────────────────────
    def get_backtest_validator(self) -> BacktestValidatorAgent:
        return self.backtest_validator

    # ── Expose drawdown_guard pour bot.py ─────────────────────────────────
    def get_drawdown_guard(self) -> DrawdownGuardAgent:
        return self.drawdown_guard

    # ────────────────────────────────────────────────────────────────────────
    # DEMANDE COLLECTIVE
    # ────────────────────────────────────────────────────────────────────────

    async def ask_all(
        self, question: str, context: dict
    ) -> Tuple[List[Dict], Dict]:
        logger.info(f"[ORCHESTRATOR] 🚀 Analyse collective → {question[:80]}...")

        # Injection du glossaire commun
        context["shared_glossary"] = self.kb.get_glossary()

        # ── Phase 0 : Vérifications de sécurité (veto précoce) ───────────
        # 0a. Santé système
        immune_status = await self.self_improvement.safe_respond("monitor health", context)
        context["immune_health"] = immune_status.get("score", 100)

        # 0b. Code health (DevOps IA)
        code_health = await self.code_fixer.safe_respond("code diagnostic health", context)
        context["code_health"] = code_health.get("health_score", 100)

        # 0c. Circuit breaker DrawdownGuard — VETO ABSOLU (non contournable)
        guard_resp = await self.drawdown_guard.safe_respond("circuit breaker drawdown guard", context)
        context["drawdown_guard"] = guard_resp
        if guard_resp.get("veto"):
            logger.warning(
                f"[ORCHESTRATOR] 🛑 VETO DrawdownGuard: {guard_resp.get('veto_reason')} "
                f"| {guard_resp.get('summary','')[:60]}"
            )
            no_trade_resp = {
                "agent": "orchestrator",
                "summary": guard_resp.get("summary", "Circuit breaker actif"),
                "confidence": 1.0,
                "recommendation": guard_resp.get("recommendation", "NO TRADE"),
                "veto_source": "drawdown_guard",
                "telegram_alert": guard_resp.get("telegram_alert", False),
            }
            return [guard_resp], no_trade_resp

        # 0d. NewsEventAgent — veto sur événements macro critiques
        news_resp = await self.news_event.safe_respond("news event macro critical", context)
        context["news_event"] = news_resp
        if news_resp.get("veto"):
            logger.warning(
                f"[ORCHESTRATOR] 📰 VETO News: {news_resp.get('events', ['?'])[0][:60] if news_resp.get('events') else 'événement critique'}"
            )
            return [news_resp], {
                "agent": "orchestrator",
                "summary": news_resp.get("summary", "Pause événement macro"),
                "confidence": 1.0,
                "recommendation": news_resp.get("recommendation", "NO TRADE"),
                "veto_source": "news_event",
            }

        # Détection crash précédent
        restart_reason = self._check_for_crash_flag()
        if restart_reason:
            context["restart_reason"] = restart_reason
            logger.warning(f"[ORCHESTRATOR] Crash précédent détecté: {restart_reason}")

        # ── Phase 1 : Collecte données parallèle ─────────────────────────
        symbol = context.get("symbol", "BTCUSDT")
        phase1_tasks = [
            ("quant_ml",        self.quant_ml.safe_respond(
                "market regime bull bear sideways volatile", context)),
            ("research",        self.research.safe_respond(
                f"analyse recherche on-chain order book {symbol}", context)),
            ("social_listener", self.social_listener.safe_respond(
                f"social sentiment news {symbol}", context)),
            # Nouveaux agents V6 — signal en parallèle
            ("order_book",      self.order_book.safe_respond(
                f"order book imbalance bid ask {symbol}", context)),
            ("funding_rate",    self.funding_rate.safe_respond(
                f"funding rate perpetual futures {symbol}", context)),
            ("correlation",     self.correlation_watcher.safe_respond(
                f"correlation portfolio positions {symbol}", context)),
        ]

        phase1_results = {}
        p1_done = await asyncio.gather(
            *[t for _, t in phase1_tasks],
            return_exceptions=True
        )
        for (name, _), result in zip(phase1_tasks, p1_done):
            if isinstance(result, Exception):
                logger.error(f"[ORCHESTRATOR] Phase1 {name} error: {result}")
                phase1_results[name] = {
                    "agent": name, "summary": f"Erreur: {result}",
                    "confidence": 0.0, "recommendation": "HOLD"
                }
            else:
                phase1_results[name] = result

        # Veto FundingRate (LONG en funding extrême)
        funding_resp = phase1_results.get("funding_rate", {})
        if funding_resp.get("veto") and context.get("side", "LONG") in ("LONG", "BUY"):
            logger.warning(f"[ORCHESTRATOR] 💸 VETO FundingRate: {funding_resp.get('summary','')[:60]}")
            return [funding_resp], {
                "agent": "orchestrator",
                "summary": funding_resp.get("summary", "Veto funding extrême"),
                "confidence": 1.0,
                "recommendation": funding_resp.get("recommendation", "NO TRADE"),
                "veto_source": "funding_rate",
            }

        # Enrichissement contexte avec Phase 1
        size_reduction = max(
            phase1_results.get("correlation", {}).get("size_reduction", 0.0),
            news_resp.get("size_reduction", 0.0),
            funding_resp.get("size_reduction", 0.0),
        )
        enriched_ctx = {
            **context,
            "macro":             phase1_results["quant_ml"].get("regime", "NEUTRAL"),
            "macro_trend":       phase1_results["quant_ml"].get("regime", "NEUTRAL"),
            "fg_value":          phase1_results["quant_ml"].get("fg_live", 50),
            "rsi":               phase1_results["research"].get("rsi", 50),
            "funding_rate_val":  funding_resp.get("funding_rate", 0.0),
            "sentiment":         phase1_results["social_listener"].get("sentiment_score", 0.5),
            "is_critical":       phase1_results["social_listener"].get("is_critical", False),
            "orderbook_signal":  phase1_results["order_book"].get("signal", "NEUTRAL"),
            "orderbook_imb":     phase1_results["order_book"].get("imbalance", 0.5),
            "portfolio_corr":    phase1_results["correlation"].get("avg_corr", 0.0),
            "size_reduction":    size_reduction,
        }

        # ── Phase 2 : Analyse & Risque (dépend de Phase 1) ───────────────
        phase2_tasks = [
            ("analyst",              self.analyst.safe_respond(question, enriched_ctx)),
            ("risk",                 self.risk.safe_respond(question, enriched_ctx)),
            ("knowledge_specialist", self.knowledge_specialist.safe_respond(
                f"wyckoff vsa smart money analyse {symbol}", enriched_ctx)),
        ]
        phase2_results = {}
        p2_done = await asyncio.gather(*[t for _, t in phase2_tasks], return_exceptions=True)
        for (name, _), result in zip(phase2_tasks, p2_done):
            if isinstance(result, Exception):
                phase2_results[name] = {
                    "agent": name, "summary": f"Erreur: {result}",
                    "confidence": 0.0, "recommendation": "HOLD"
                }
            else:
                phase2_results[name] = result

        enriched_ctx["analysis"] = phase2_results["analyst"]
        enriched_ctx["risk"]     = phase2_results["risk"]

        # ── Phase 3 : Décision trading ────────────────────────────────────
        trader_resp = await self.trader.safe_respond(question, enriched_ctx)
        enriched_ctx["trader_decision"] = trader_resp

        # ── Phase 4 : Débat multi-rounds ─────────────────────────────────
        self.debate_rounds = 0
        current_confidence = trader_resp.get("confidence", 0.0)
        final_responses: List[Dict] = []

        for resp in [*phase1_results.values(), *phase2_results.values(), trader_resp]:
            if isinstance(resp, dict):
                final_responses.append(resp)

        MAX_ROUNDS  = 3
        CONF_TARGET = 0.85

        while self.debate_rounds < MAX_ROUNDS and current_confidence < CONF_TARGET:
            self.debate_rounds += 1
            logger.info(
                f"[ORCHESTRATOR] Débat round {self.debate_rounds} | "
                f"Confiance: {current_confidence:.2f}"
            )

            enriched_ctx["agent_outputs"]    = final_responses
            enriched_ctx["final_confidence"] = current_confidence
            enriched_ctx["debate_rounds"]    = self.debate_rounds

            debate_tasks = [
                self.trader.safe_respond("raffine décision — débat cerveau collectif", enriched_ctx),
                self.risk.safe_respond("raffine risk — débat cerveau collectif", enriched_ctx),
            ]
            debate_results = await asyncio.gather(*debate_tasks, return_exceptions=True)
            for result in debate_results:
                if isinstance(result, dict):
                    final_responses.append(result)
                    new_conf = result.get("confidence", current_confidence)
                    if new_conf > current_confidence:
                        current_confidence = new_conf

        # ── Phase 5 : Synthèse finale ────────────────────────────────────
        enriched_ctx["agent_outputs"]    = final_responses
        enriched_ctx["trader_decision"]  = trader_resp
        enriched_ctx["final_confidence"] = current_confidence
        enriched_ctx["debate_rounds"]    = self.debate_rounds

        final = await self.supervisor.safe_respond(
            "Synthétise tout avec le glossaire commun et donne décision finale claire : TRADE ou NO TRADE",
            enriched_ctx
        )

        # Appliquer réduction de taille si signalée
        if size_reduction > 0:
            final["size_reduction"] = size_reduction
            final["size_note"] = (
                f"Taille réduite de {size_reduction:.0%} "
                f"(news: {news_resp.get('size_reduction',0):.0%} | "
                f"corr: {phase1_results.get('correlation',{}).get('size_reduction',0):.0%} | "
                f"funding: {funding_resp.get('size_reduction',0):.0%})"
            )
            logger.info(f"[ORCHESTRATOR] ⚖️ Réduction taille appliquée: -{size_reduction:.0%}")

        logger.info(
            f"[ORCHESTRATOR] ✅ Terminé après {self.debate_rounds} rounds "
            f"→ confiance {current_confidence:.2f} | "
            f"Décision: {trader_resp.get('decision', 'HOLD')}"
        )
        return final_responses, final

    # ────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ────────────────────────────────────────────────────────────────────────

    def _enrich_context(self, context: dict) -> dict:
        return {**context, "shared_glossary": self.kb.get_glossary()}

    def _check_for_crash_flag(self) -> str | None:
        flag_file = ".crash_flag"
        if os.path.exists(flag_file):
            try:
                with open(flag_file, "r") as f:
                    reason = f.read().strip()
                os.remove(flag_file)
                return reason
            except Exception:
                pass
        return None

    async def run(self, market_data: dict, memory: dict) -> Dict[str, Any]:
        symbol = market_data.get("symbol", "UNKNOWN")
        context = {
            "market_data":     market_data,
            "memory":          memory,
            "symbol":          symbol,
            "shared_glossary": self.kb.get_glossary(),
        }
        responses, final = await self.ask_all(
            f"Analyse complète du marché {symbol} et propose une décision de trade",
            context
        )
        return {
            "responses":      responses,
            "final_decision": final,
            "debate_rounds":  self.debate_rounds,
            "status":         "ok",
        }
