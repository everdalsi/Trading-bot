"""
🎯 ORCHESTRATOR V3 — Multi-agents + Mémoire infinie + Bugs corrigés
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Corrections vs V2 :
[BUG FIX] Injection des stats LearningAgent dans le contexte avant ask_all
[BUG FIX] Injection des stats PerformanceTracker dans le contexte
[BUG FIX] Supervisor reçoit toujours trader_decision et risk dans son contexte
[AMÉLIORATION] Contexte enrichi transmis à tous les agents
[AMÉLIORATION] Décision finale plus robuste (score composite)
"""

import asyncio
from typing import Dict, Any, List, Tuple

from agents.analyst_agent import AnalystAgent
from agents.risk_agent import RiskAgent
from agents.trader_agent import TraderAgent
from knowledge_base import KnowledgeBase
from agents.supervisor_agent import SupervisorAgent
from agents.learning_agent import LearningAgent
from agents.performance_tracker import PerformanceTracker
from agents.research_agent import ResearchAgent


class Orchestrator:

    def __init__(self):
        self.analyst    = AnalystAgent()
        self.risk       = RiskAgent()
        self.trader     = TraderAgent()
        self.supervisor = SupervisorAgent()
        self.learning   = LearningAgent()
        self.performance = PerformanceTracker()
        self.research   = ResearchAgent()
        self.knowledge  = KnowledgeBase()

    # ─────────────────────────────────────────────────────────────
    #  ask_all — Interroge tous les agents en parallèle
    # ─────────────────────────────────────────────────────────────
    async def ask_all(
        self, question: str, context: dict
    ) -> Tuple[List[Dict], Dict]:
        print(f"[ORCHESTRATOR] ask_all → {question[:80]}...")

        enriched_ctx = self._enrich_context(context)

        tasks = [
            self.analyst.respond(question, enriched_ctx),
            self.risk.respond(question, enriched_ctx),
            self.trader.respond(question, enriched_ctx),
            self.learning.respond(question, enriched_ctx),
            self.research.respond(question, enriched_ctx),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        responses = []
        agent_names = ["analyst", "risk", "trader", "learning", "research"]
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                responses.append({
                    "agent": agent_names[i],
                    "summary": f"Erreur interne: {str(res)[:100]}",
                    "arguments": [],
                    "risks": [],
                    "confidence": 0.0,
                    "recommendation": "Vérifier l'agent",
                })
            else:
                responses.append(res)

        trader_resp = next((r for r in responses if r.get("agent") == "trader"), {})
        risk_resp   = next((r for r in responses if r.get("agent") == "risk"), {})

        supervisor_ctx = {
            **enriched_ctx,
            "agent_outputs": responses,
            "trader_decision": trader_resp,
            "risk": risk_resp,
            "score": enriched_ctx.get("global_score", 0.5),
        }

        final = await self.supervisor.respond(question, supervisor_ctx)

        print(
            f"[ORCHESTRATOR] ask_all terminé → {len(responses)} réponses | "
            f"Final: {final.get('summary', '')[:80]}..."
        )
        return responses, final

    # ─────────────────────────────────────────────────────────────
    #  run — Pipeline de trading complet
    # ─────────────────────────────────────────────────────────────
    async def run(self, market_data: dict, memory: dict) -> Dict[str, Any]:
        symbol = market_data.get("symbol", "UNKNOWN")

        context = {
            "symbol": symbol,
            "market_data": market_data,
            "memory": memory,
            "sim": memory.get("sim", {}),
            "base_confidence": 0.65,
        }

        context = self._enrich_context(context)

        blacklist_check = await self.learning.respond(
            "should I blacklist this symbol?", context
        )
        if blacklist_check.get("recommendation", "").lower().startswith("⛔"):
            return {
                "decision": "NO TRADE",
                "reason": "learning_blacklist",
                "score": 0.0,
            }

        try:
            analysis, risk_result, trader_decision = await asyncio.gather(
                self.analyst.respond("analyze current market", context),
                self.risk.respond("assess risk", context),
                self.trader.respond("make trading decision", context),
            )

            context.update({
                "analysis": analysis,
                "risk": risk_result,
                "trader_decision": trader_decision,
            })

            learning_result = await self.learning.respond(
                "compute global and symbol score", context
            )

            final_score = learning_result.get("confidence", 0.5)
            context["score"] = final_score

            final = await self.supervisor.respond("validate final decision", context)

            if not final or final.get("decision") == "NO TRADE":
                return {
                    "decision": "NO TRADE",
                    "reason": final.get("reason", "supervisor_rejected"),
                    "score": final_score,
                }

            position_size = self.get_position_size(
                balance=1000,
                risk_per_trade=0.02,
                confidence=final_score,
            )
            final["position_size"] = position_size

            if final.get("decision") in ("BUY", "SELL"):
                trade_entry = {
                    "symbol": symbol,
                    "decision": final["decision"],
                    "confidence": final_score,
                    "result": "pending",
                    "timestamp": "now",
                }
                try:
                    memory.setdefault("trades", []).append(trade_entry)
                except Exception:
                    pass
                try:
                    self.performance.log_trade({
                        "symbol": symbol,
                        "decision": final["decision"],
                        "confidence": final_score,
                    })
                except Exception:
                    pass

            print(
                f"\n===== ORCHESTRATOR DECISION =====\n"
                f"Symbol     : {symbol}\n"
                f"Score final: {final_score:.3f}\n"
                f"Decision   : {final.get('decision')}\n"
                f"Position   : ${position_size}\n"
                f"Reason     : {final.get('reason', 'N/A')}\n"
                f"================================="
            )
            return final

        except Exception as e:
            print(f"[ORCHESTRATOR ERROR] {e}")
            return {"decision": "ERROR", "reason": str(e)[:100], "score": 0.0}

    # ─────────────────────────────────────────────────────────────
    #  ENRICHISSEMENT DU CONTEXTE
    # ─────────────────────────────────────────────────────────────
    def _enrich_context(self, context: dict) -> dict:
        enriched = dict(context)
        enriched["extreme_learning_mode"] = True         
        enriched["learning_mode"] = True

        try:
            symbol = context.get("symbol")
            global_stats  = self.learning.get_global_stats_db(window=100)
            symbol_stats  = self.learning.get_symbol_stats_db(symbol, window=20) if symbol else global_stats
            best_patterns = self.learning.get_best_patterns(symbol, limit=3)
            worst_patterns= self.learning.get_worst_patterns(symbol, limit=3)
            auto_rules    = self.learning.get_auto_rules()
            insights      = self.learning.get_active_insights(limit=3)
            lesson_count  = self.learning.get_lesson_count()

            enriched.update({
                "global_score":   global_stats["score"],
                "symbol_score":   symbol_stats["score"],
                "lesson_count":   lesson_count,
                "best_patterns":  best_patterns,
                "worst_patterns": worst_patterns,
                "auto_rules":     auto_rules,
                "insights":       insights,
            })
        except Exception as e:
            print(f"[ORCHESTRATOR] enrich learning error: {e}")

        try:
            memory = context.get("memory", {})
            if memory:
                stats = self.performance.get_global_stats(memory)
                enriched.update({
                    "wr_live":       stats["winrate"],
                    "wins_live":     stats["wins"],
                    "losses_live":   stats["losses"],
                    "total_trades":  stats["total_trades"],
                    "sharpe":        stats.get("sharpe", 0.0),
                    "profit_factor": stats.get("profit_factor", 0.0),
                    "streak_type":   stats.get("streak_type", "neutral"),
                    "streak_count":  stats.get("streak_count", 0),
                    "degraded":      stats.get("degraded", False),
                })
        except Exception as e:
            print(f"[ORCHESTRATOR] enrich perf error: {e}")

        # === Knowledge Base RAG ===
        enriched["knowledge"] = self.knowledge
        enriched["knowledge_context"] = self.knowledge.get_context_for_agent(
            f"Contexte trading sur {context.get('symbol', 'marché crypto')} - CFA, Wyckoff, VSA, Trading for a Living"
        )

        return enriched

    # ─────────────────────────────────────────────────────────────
    #  POSITION SIZING
    # ─────────────────────────────────────────────────────────────
    def get_position_size(
        self, balance: float, risk_per_trade: float, confidence: float
    ) -> float:
        try:
            base_risk = balance * risk_per_trade
            adjusted  = base_risk * max(0.3, confidence)
            return round(adjusted, 2)
        except Exception:
            return 0.0

    # ─────────────────────────────────────────────────────────────
    #  AUTO-AJUSTEMENT + AUTO-EXÉCUTION
    # ─────────────────────────────────────────────────────────────
    async def self_tune_and_execute(self, memory: dict):
        if not EXTREME_LEARNING_MODE:
            return

        lesson_count = self.learning.get_lesson_count()

        if lesson_count < 1000:
            global MICRO_CONF_MIN, MAX_MICRO_POSITIONS, CYCLE_MICRO
            MICRO_CONF_MIN = max(3, MICRO_CONF_MIN - 2)
            MAX_MICRO_POSITIONS = min(200, MAX_MICRO_POSITIONS + 20)
            CYCLE_MICRO = max(5, CYCLE_MICRO - 2)

            print(f"[SELF-TUNE] Leçons = {lesson_count} → paramètres agressifs : conf_min={MICRO_CONF_MIN}, max_pos={MAX_MICRO_POSITIONS}, cycle={CYCLE_MICRO}s")

            global LEARN_MODE_MAX_PCT, MICRO_MAX_PCT
            LEARN_MODE_MAX_PCT = max(0.48, LEARN_MODE_MAX_PCT)
            MICRO_MAX_PCT = max(0.48, MICRO_MAX_PCT)
            print(f"[SELF-TUNE] Volume max forcé à {LEARN_MODE_MAX_PCT*100:.0f}% par trade")

        try:
            best_symbol = self.learning.get_best_patterns()[0]["pattern"].split()[0] if self.learning.get_best_patterns() else "BTCUSDT"
            context = self._enrich_context({"symbol": best_symbol, "memory": memory})
            trader_resp = await self.trader.respond("force micro trade maintenant", context)
            if trader_resp.get("decision") == "BUY":
                print(f"[AUTO-EXEC] Ouverture forcée sur {best_symbol} (apprentissage extrême)")
        except:
            pass
