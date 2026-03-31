"""
🎯 ORCHESTRATOR V7 — PARALLÉLISME TOTAL + Cerveau Collectif Expert
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UPGRADES V7 (critique pour la vitesse) :
- asyncio.gather() sur TOUS les agents indépendants (×4-6 plus rapide)
- Timeout individuel par agent (évite le blocage global)
- Multi-symbol : analyse en parallèle sur N symboles simultanément
- Circuit breakers en Phase 0 (veto instantané avant le débat)
- Consensus bayésien amélioré avec poids dynamiques par régime
- Retry automatique sur agents défaillants
- Agrégation des résultats en temps réel (stream-style)
"""

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

from logging_config import logger

# Timeout par agent (secondes)
AGENT_TIMEOUT = 12.0
PHASE0_TIMEOUT = 5.0


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

        # ── Agents V6.0 — Sécurité & Signal ─────────────────────────────
        self.news_event          = NewsEventAgent()
        self.order_book          = OrderBookAgent()
        self.funding_rate        = FundingRateAgent()
        self.drawdown_guard      = DrawdownGuardAgent()
        self.correlation_watcher = CorrelationWatcherAgent()
        self.backtest_validator  = BacktestValidatorAgent()

        # ── Agents V8 — Edge Arbitrage & Snipe ───────────────────────────
        self.polymarket_arb  = PolymarketArbAgent()    # Spread Polymarket vs CEX
        self.event_sniper    = EventSniperAgent()       # Liquidations/OI/funding/volume

        self.debate_rounds = 0
        logger.info("[ORCHESTRATOR V7] ✅ Tous les agents initialisés — Mode PARALLÈLE activé (+ PolyArb + Sniper)")

    def get_backtest_validator(self) -> BacktestValidatorAgent:
        return self.backtest_validator

    def get_drawdown_guard(self) -> DrawdownGuardAgent:
        return self.drawdown_guard

    # ────────────────────────────────────────────────────────────────────────
    # PHASE 0 : VÉRIFICATIONS DE SÉCURITÉ (parallèles, veto instantané)
    # ────────────────────────────────────────────────────────────────────────

    async def _phase0_security(self, context: dict) -> Tuple[bool, Dict[str, Any]]:
        """
        Vérifications de sécurité en PARALLÈLE.
        Retourne (veto, veto_response) si un veto est déclenché.
        """
        q_immune  = "monitor health"
        q_code    = "code diagnostic health"
        q_guard   = "circuit breaker drawdown guard"
        q_news    = "news event macro critical"
        q_funding = "funding rate check"

        # ── Exécution PARALLÈLE des 5 vérifications ───────────────────────
        results = await asyncio.gather(
            _safe_call(self.self_improvement,  q_immune,  context, PHASE0_TIMEOUT),
            _safe_call(self.code_fixer,        q_code,    context, PHASE0_TIMEOUT),
            _safe_call(self.drawdown_guard,    q_guard,   context, PHASE0_TIMEOUT),
            _safe_call(self.news_event,        q_news,    context, PHASE0_TIMEOUT),
            _safe_call(self.funding_rate,      q_funding, context, PHASE0_TIMEOUT),
            return_exceptions=False
        )

        immune_resp, code_resp, guard_resp, news_resp, funding_resp = results

        # Enrichir le contexte
        context["immune_health"] = immune_resp.get("score", 100)
        context["code_health"]   = code_resp.get("health_score", 100)
        context["drawdown_guard"] = guard_resp
        context["news_event"]    = news_resp
        context["funding_rate"]  = funding_resp

        # ── Vérifier vetos ────────────────────────────────────────────────
        # DrawdownGuard : veto absolu (non contournable)
        if guard_resp.get("veto"):
            logger.warning(f"[ORCH V7] 🛑 VETO DrawdownGuard: {guard_resp.get('veto_reason')}")
            return True, {
                "agent": "orchestrator", "summary": guard_resp.get("summary", "Circuit breaker"),
                "confidence": 1.0, "recommendation": "NO TRADE", "veto_source": "drawdown_guard",
            }

        # News : veto sur événement macro critique
        if news_resp.get("veto"):
            logger.warning(f"[ORCH V7] 📰 VETO News: {news_resp.get('summary', '')[:60]}")
            return True, {
                "agent": "orchestrator", "summary": news_resp.get("summary", "Pause événement macro"),
                "confidence": 1.0, "recommendation": "NO TRADE", "veto_source": "news_event",
            }

        # Funding rate trop élevé
        if funding_resp.get("veto"):
            logger.warning(f"[ORCH V7] 💰 VETO FundingRate: {funding_resp.get('summary', '')[:60]}")
            return True, {
                "agent": "orchestrator", "summary": funding_resp.get("summary", "Funding rate trop élevé"),
                "confidence": 1.0, "recommendation": "NO TRADE", "veto_source": "funding_rate",
            }

        return False, {}

    # ────────────────────────────────────────────────────────────────────────
    # DEMANDE COLLECTIVE PARALLÈLE
    # ────────────────────────────────────────────────────────────────────────

    async def ask_all(
        self, question: str, context: dict
    ) -> Tuple[List[Dict], Dict]:
        logger.info(f"[ORCH V7] 🚀 Analyse parallèle → {question[:80]}")

        # Glossaire partagé
        context["shared_glossary"] = self.kb.get_glossary()

        # ── Phase 0 : sécurité PARALLÈLE ──────────────────────────────────
        veto, veto_resp = await self._phase0_security(context)
        if veto:
            return [veto_resp], veto_resp

        # ── Phase 1 : agents d'analyse PARALLÈLES ─────────────────────────
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
            # ── Agents V8 : edge arbitrage + snipe (indépendants) ──
            _safe_call(self.polymarket_arb,       question, context, timeout=8.0),
            _safe_call(self.event_sniper,         question, context, timeout=10.0),
            return_exceptions=False
        )

        (analyst_resp, quant_resp, ob_resp, social_resp,
         research_resp, ks_resp, wc_resp, corr_resp,
         poly_arb_resp, sniper_resp) = group_a

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

        # Log si edge actif
        if poly_arb_resp.get("signal") != "HOLD":
            logger.info(f"[ORCH V8] 🏦 PolyArb signal: {poly_arb_resp['signal']} spread={context['poly_arb_spread']:.2f}%")
        if sniper_resp.get("should_emit"):
            logger.info(f"[ORCH V8] 🎯 Sniper signal: {sniper_resp['signal']} conf={sniper_resp['confidence']:.0%}")

        # ── Phase 2 : agents de risque et de décision PARALLÈLES ──────────
        group_b = await asyncio.gather(
            _safe_call(self.risk,     question, context),
            _safe_call(self.hedging,  question, context),
            return_exceptions=False
        )
        risk_resp, hedging_resp = group_b

        context["risk"]             = risk_resp
        context["risk_level"]       = risk_resp.get("risk_level", "MODERATE")
        context["kelly_adjusted"]   = risk_resp.get("kelly_adjusted", 0.05)

        # Veto risk CRITICAL
        if risk_resp.get("risk_level") in ("CRITICAL",):
            logger.warning(f"[ORCH V7] ⚠️ VETO Risk CRITICAL")
            return [risk_resp], {
                "agent": "orchestrator", "summary": risk_resp.get("summary", "Risque critique"),
                "confidence": 1.0, "recommendation": "NO TRADE", "veto_source": "risk",
            }

        # ── Phase 3 : décision trader (séquentielle — dépend de phases 1&2) ──
        trader_resp = await _safe_call(self.trader, question, context)

        # ── Agréger tous les résultats ────────────────────────────────────
        all_outputs = [
            analyst_resp, quant_resp, ob_resp, social_resp,
            research_resp, ks_resp, wc_resp, corr_resp,
            risk_resp, hedging_resp, trader_resp,
            poly_arb_resp, sniper_resp,
        ]
        # Filtrer les réponses vides
        all_outputs = [r for r in all_outputs if r and isinstance(r, dict)]

        context["agent_outputs"]    = all_outputs
        context["global_score"]     = self._compute_global_score(all_outputs)
        context["final_confidence"] = context["global_score"]
        context["debate_rounds"]    = self.debate_rounds

        # ── Phase 4 : supervision finale (séquentielle) ────────────────────
        final = await _safe_call(self.supervisor, question, context)

        self.debate_rounds += 1
        logger.info(
            f"[ORCH V7] ✅ Décision: {final.get('decision', '?')} | "
            f"conf: {final.get('confidence', 0):.0%} | "
            f"{len(all_outputs)} agents | Parallèle ✓"
        )
        return all_outputs, final

    # ────────────────────────────────────────────────────────────────────────
    # ANALYSE MULTI-SYMBOLES EN PARALLÈLE
    # ────────────────────────────────────────────────────────────────────────

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

    # ────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ────────────────────────────────────────────────────────────────────────

    def _compute_global_score(self, outputs: List[Dict]) -> float:
        """Score global pondéré sur tous les agents."""
        WEIGHTS = {
            "analyst": 0.20, "risk": 0.18, "quant_ml": 0.12,
            "order_book": 0.10, "trader": 0.15, "research": 0.08,
            "social_listener": 0.05, "hedging": 0.05,
            "knowledge_specialist": 0.04, "wallet_copier": 0.03,
        }
        total_w, weighted_sum = 0.0, 0.0
        for out in outputs:
            name = out.get("agent", "")
            conf = float(out.get("confidence", 0.5))
            reco = str(out.get("recommendation", out.get("decision", "HOLD"))).upper()
            w = WEIGHTS.get(name, 0.02)
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
