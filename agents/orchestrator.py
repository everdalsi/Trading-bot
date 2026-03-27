"""
🎯 ORCHESTRATOR V3 — Multi-agents + Mémoire infinie + Bugs corrigés + Cerveau Collectif
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ajout de la phase de collaboration : les agents discutent entre eux avant la décision finale
"""

import asyncio
from typing import Dict, Any, List, Tuple
import os

from agents.analyst_agent import AnalystAgent
from agents.risk_agent import RiskAgent
from agents.trader_agent import TraderAgent
from knowledge_base import KnowledgeBase
from agents.supervisor_agent import SupervisorAgent
from agents.learning_agent import LearningAgent
from agents.performance_tracker import PerformanceTracker
from agents.research_agent import ResearchAgent
from agents.knowledge_specialist_agent import KnowledgeSpecialistAgent
from agents.self_improvement import SelfImprovementEngineer
from agents.wallet_copier_agent import WalletCopierAgent
from agents.social_listener_agent import SocialListenerAgent   # ← UPGRADE AJOUTÉE

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
        self.knowledge_specialist = KnowledgeSpecialistAgent()
        self.self_improvement = SelfImprovementEngineer(orchestrator=self)
        self.wallet_copier = WalletCopierAgent()
        self.social_listener = SocialListenerAgent()   # ← UPGRADE AJOUTÉE
        self.debate_rounds = 0

    async def ask_all(
        self, question: str, context: dict
    ) -> Tuple[List[Dict], Dict]:
        print(f"[ORCHESTRATOR] ask_all → {question[:80]}...")

        # === UPGRADE : Vérification ImmuneSystem avant toute décision ===
        immune_status = await self.self_improvement.respond("monitor health", context)
        context["immune_health"] = immune_status.get("score", 100)

        restart_reason = self._check_for_crash_flag()
        if restart_reason:
            context["restart_reason"] = restart_reason

        enriched_ctx = self._enrich_context(context)

        # === PHASE 1 : Appel parallèle initial ===
        tasks = [
            self.analyst.respond(question, enriched_ctx),
            self.risk.respond(question, enriched_ctx),
            self.trader.respond(question, enriched_ctx),
            self.learning.respond(question, enriched_ctx),
            self.research.respond(question, enriched_ctx),
            self.knowledge_specialist.respond(question, enriched_ctx),
            self.wallet_copier.respond(question, enriched_ctx),
            self.social_listener.respond(question, enriched_ctx),   # ← UPGRADE AJOUTÉE
            self.self_improvement.respond(question, enriched_ctx),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        responses = []
        agent_names = ["analyst", "risk", "trader", "learning", "research", "knowledge_specialist", "wallet_copier", "social_listener", "self_improvement"]
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

        # === PHASE DÉBAT COLLECTIF MULTI-ROUNDS (jusqu’à ≥ 99 % + veto dur) ===
        current_confidence = 0.0
        max_rounds = 7  # UPGRADE : on donne plus de rounds pour atteindre 99%
        self.debate_rounds = 0

        while current_confidence < 0.99 and self.debate_rounds < max_rounds:
            self.debate_rounds += 1
            print(f"[ORCHESTRATOR] Débat collectif round {self.debate_rounds}/7 — confiance actuelle {current_confidence:.2f}")

            collaboration_ctx = {
                **enriched_ctx,
                "agent_outputs": responses,
                "previous_round": responses,
                "debate_round": self.debate_rounds,
                "target_confidence": 0.99,          # ← UPGRADE : seuil passé à 99%
                "strict_veto_mode": True            # ← UPGRADE : activation du mode veto dur
            }

            collab_tasks = [
                self.analyst.respond("Raffine ton analyse en tenant compte des réponses des autres agents et vise une confiance ≥ 99 %", collaboration_ctx),
                self.risk.respond("Raffine ton analyse en tenant compte des réponses des autres agents et vise une confiance ≥ 99 %", collaboration_ctx),
                self.trader.respond("Raffine ton analyse en tenant compte des réponses des autres agents et vise une confiance ≥ 99 %", collaboration_ctx),
                self.learning.respond("Raffine ton analyse en tenant compte des réponses des autres agents et vise une confiance ≥ 99 %", collaboration_ctx),
                self.research.respond("Raffine ton analyse en tenant compte des réponses des autres agents et vise une confiance ≥ 99 %", collaboration_ctx),
                self.knowledge_specialist.respond("Raffine ton analyse en tenant compte des réponses des autres agents et vise une confiance ≥ 99 %", collaboration_ctx),
                self.wallet_copier.respond("Raffine ton analyse en tenant compte des réponses des autres agents et vise une confiance ≥ 99 %", collaboration_ctx),
                self.social_listener.respond("Raffine ton analyse en tenant compte des réponses des autres agents et vise une confiance ≥ 99 %", collaboration_ctx),   # ← UPGRADE AJOUTÉE
            ]

            collab_results = await asyncio.gather(*collab_tasks, return_exceptions=True)

            collab_responses = []
            for i, res in enumerate(collab_results):
                if isinstance(res, Exception):
                    collab_responses.append(responses[i])
                else:
                    collab_responses.append(res)

            # === VETO DUR Risk + Learning (priorité absolue – winrate parfait) ===
            risk_resp = next((r for r in collab_responses if r.get("agent") == "risk"), {})
            learn_resp = next((r for r in collab_responses if r.get("agent") == "learning"), {})

            if risk_resp.get("risk_level") in ["CRITICAL", "HIGH"] or "STOP" in str(risk_resp.get("recommendation", "")).upper():
                return collab_responses, {
                    "decision": "NO TRADE",
                    "reason": "VETO RISK TOTAL",
                    "score": 0.0
                }

            if learn_resp.get("blacklist", False) or "perdant" in str(learn_resp.get("summary", "")).lower():
                return collab_responses, {
                    "decision": "NO TRADE",
                    "reason": "VETO LEARNING (trade perdant détecté)",
                    "score": 0.0
                }

            final_responses = collab_responses
            current_confidence = max((r.get("confidence", 0) for r in final_responses if isinstance(r, dict)), default=0.0)

        trader_resp = next((r for r in final_responses if r.get("agent") == "trader"), {})
        risk_resp   = next((r for r in final_responses if r.get("agent") == "risk"), {})

        supervisor_ctx = {
            **enriched_ctx,
            "agent_outputs": final_responses,
            "trader_decision": trader_resp,
            "risk": risk_resp,
            "score": enriched_ctx.get("global_score", 0.5),
            "collaboration_round": True,
            "debate_rounds": self.debate_rounds,
            "final_confidence": current_confidence
        }

        final = await self.supervisor.respond("Synthétise la discussion collective et donne la décision finale", supervisor_ctx)

        print(
            f"[ORCHESTRATOR] ask_all terminé → {len(final_responses)} réponses après {self.debate_rounds} rounds de débat (confiance finale {current_confidence:.2f})"
        )
        return final_responses, final

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
        if blacklist_check.get("recommendation", "").lower().startswith("no"):
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

            final = await self.ask_all("Prends la décision finale de trading", context)
            return final[1] if isinstance(final, tuple) else final

        except Exception as e:
            print(f"[ORCHESTRATOR] Erreur critique dans run : {e}")
            return {"decision": "NO TRADE", "reason": f"Exception orchestrator: {e}", "score": 0.0}
