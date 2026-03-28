"""
🎯 ORCHESTRATOR V5 — Cerveau Collectif Parfait + Zéro Malentendu + Spécialisation Forcée
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Upgrade BaseAgent V3 + glossaire partagé + safe_respond partout + vérif rôle stricte
+ Intégration QuantML / ExecutionEngine / YieldStaking dans le débat collectif
"""

import asyncio
from typing import Dict, Any, List, Tuple
import os

from agents.base_agent import BaseAgent
from agents.analyst_agent import AnalystAgent
from agents.risk_agent import RiskAgent
from agents.trader_agent import TraderAgent
from knowledge_base import KnowledgeBase
from agents.supervisor_agent import SupervisorAgent
from agents.learning_agent import LearningAgent
from agents.performance_tracker import PerformanceTracker
from agents.research_agent import ResearchAgent
from agents.knowledge_specialist_agent import KnowledgeSpecialistAgent
from agents.evolution_agent import EvolutionAgent as SelfImprovementEngineer
from agents.wallet_copier_agent import WalletCopierAgent
from agents.social_listener_agent import SocialListenerAgent

# ← NOUVEAUX AGENTS V8 (intégrés au cerveau collectif)
from agents.quant_ml_agent import QuantMLAgent
from agents.execution_engine_agent import ExecutionEngineAgent
from agents.yield_staking_agent import YieldStakingAgent

class Orchestrator:

    def __init__(self):
        self.kb = KnowledgeBase()

        self.analyst            = AnalystAgent()
        self.risk               = RiskAgent()
        self.trader             = TraderAgent()
        self.supervisor         = SupervisorAgent()
        self.learning           = LearningAgent()
        self.performance        = PerformanceTracker()
        self.research           = ResearchAgent()
        self.knowledge_specialist = KnowledgeSpecialistAgent()
        self.self_improvement   = SelfImprovementEngineer(orchestrator=self)
        self.wallet_copier      = WalletCopierAgent()
        self.social_listener    = SocialListenerAgent()

        # === UPGRADE V5 : nouveaux agents intégrés au cerveau collectif ===
        self.quant_ml           = QuantMLAgent()
        self.execution_engine   = ExecutionEngineAgent()
        self.yield_staking      = YieldStakingAgent()

        self.debate_rounds = 0

    async def ask_all(self, question: str, context: dict) -> Tuple[List[Dict], Dict]:
        print(f"[ORCHESTRATOR V5] 🚀 Démarrage analyse collective → {question[:80]}...")

        # === Glossaire commun forcé pour zéro malentendu ===
        context["shared_glossary"] = self.kb.get_glossary()

        # === Vérification santé du système ===
        immune_status = await self.self_improvement.safe_respond("monitor health", context)
        context["immune_health"] = immune_status.get("score", 100)

        restart_reason = self._check_for_crash_flag()
        if restart_reason:
            context["restart_reason"] = restart_reason

        enriched_ctx = self._enrich_context(context)

        # === PHASE 1 : Appel parallèle de TOUS les agents (spécialisation stricte) ===
        tasks = [
            self.analyst.safe_respond(question, enriched_ctx),
            self.risk.safe_respond(question, enriched_ctx),
            self.trader.safe_respond(question, enriched_ctx),
            self.learning.safe_respond(question, enriched_ctx),
            self.research.safe_respond(question, enriched_ctx),
            self.knowledge_specialist.safe_respond(question, enriched_ctx),
            self.wallet_copier.safe_respond(question, enriched_ctx),
            self.social_listener.safe_respond(question, enriched_ctx),
            self.self_improvement.safe_respond(question, enriched_ctx),
            # === UPGRADE V5 : nouveaux agents participent au débat ===
            self.quant_ml.safe_respond(question, enriched_ctx),
            self.execution_engine.safe_respond(question, enriched_ctx),
            self.yield_staking.safe_respond(question, enriched_ctx),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        responses = []
        agent_names = [
            "analyst", "risk", "trader", "learning", "research",
            "knowledge_specialist", "wallet_copier", "social_listener",
            "self_improvement", "quant_ml", "execution_engine", "yield_staking"
        ]

        for i, res in enumerate(results):
            if isinstance(res, Exception) or "error" in str(res):
                responses.append({
                    "agent": agent_names[i],
                    "summary": f"Erreur interne: {str(res)[:100]}",
                    "confidence": 0.0,
                    "recommendation": "Vérifier rôle",
                })
            else:
                if res.get("warning"):
                    print(f"[ORCHESTRATOR V5] ⚠️ {res['agent']} hors domaine → ignoré")
                    continue
                responses.append(res)
                print(f"[ORCHESTRATOR V5] ✅ {res['agent']} a apporté : {res.get('summary','')[:60]}...")

        # === PHASE DÉBAT COLLECTIF (jusqu’à ≥ 99 % + glossaire forcé) ===
        current_confidence = 0.0
        max_rounds = 7
        self.debate_rounds = 0

        while current_confidence < 0.99 and self.debate_rounds < max_rounds:
            self.debate_rounds += 1
            print(f"[ORCHESTRATOR V5] 🔥 Débat round {self.debate_rounds}/7 — confiance {current_confidence:.2f}")

            collaboration_ctx = {
                **enriched_ctx,
                "agent_outputs": responses,
                "previous_round": responses,
                "debate_round": self.debate_rounds,
                "target_confidence": 0.99,
                "strict_veto_mode": True,
                "shared_glossary": self.kb.get_glossary(),
            }

            collab_tasks = [
                self.analyst.safe_respond(f"Raffine ton analyse avec tous les agents (glossaire commun) et vise ≥ 99 %", collaboration_ctx),
                self.risk.safe_respond(f"Raffine ton analyse avec tous les agents (glossaire commun) et vise ≥ 99 %", collaboration_ctx),
                self.trader.safe_respond(f"Raffine ton analyse avec tous les agents (glossaire commun) et vise ≥ 99 %", collaboration_ctx),
                self.learning.safe_respond(f"Raffine ton analyse avec tous les agents (glossaire commun) et vise ≥ 99 %", collaboration_ctx),
                self.research.safe_respond(f"Raffine ton analyse avec tous les agents (glossaire commun) et vise ≥ 99 %", collaboration_ctx),
                self.knowledge_specialist.safe_respond(f"Raffine ton analyse avec tous les agents (glossaire commun) et vise ≥ 99 %", collaboration_ctx),
                self.wallet_copier.safe_respond(f"Raffine ton analyse avec tous les agents (glossaire commun) et vise ≥ 99 %", collaboration_ctx),
                self.social_listener.safe_respond(f"Raffine ton analyse avec tous les agents (glossaire commun) et vise ≥ 99 %", collaboration_ctx),
                self.self_improvement.safe_respond(f"Raffine ton analyse avec tous les agents (glossaire commun) et vise ≥ 99 %", collaboration_ctx),
                self.quant_ml.safe_respond(f"Raffine ton analyse avec tous les agents (glossaire commun) et vise ≥ 99 %", collaboration_ctx),
                self.execution_engine.safe_respond(f"Raffine ton analyse avec tous les agents (glossaire commun) et vise ≥ 99 %", collaboration_ctx),
                self.yield_staking.safe_respond(f"Raffine ton analyse avec tous les agents (glossaire commun) et vise ≥ 99 %", collaboration_ctx),
            ]

            collab_results = await asyncio.gather(*collab_tasks, return_exceptions=True)

            risk_resp = next((r for r in collab_results if isinstance(r, dict) and r.get("agent") == "risk"), {})
            learn_resp = next((r for r in collab_results if isinstance(r, dict) and r.get("agent") == "learning"), {})

            if risk_resp.get("risk_level") in ["CRITICAL", "HIGH"] or "STOP" in str(risk_resp.get("recommendation", "")).upper():
                return responses, {"decision": "NO TRADE", "reason": "VETO RISK TOTAL", "score": 0.0}

            if learn_resp.get("blacklist", False):
                return responses, {"decision": "NO TRADE", "reason": "VETO LEARNING", "score": 0.0}

            final_responses = [r for r in collab_results if not isinstance(r, Exception)]
            current_confidence = max((r.get("confidence", 0) for r in final_responses), default=0.0)

        # === DÉCISION FINALE via Supervisor (claire : TRADE ou NO TRADE) ===
        final = await self.supervisor.respond(
            "Synthétise tout avec le glossaire commun et donne décision finale claire : TRADE ou NO TRADE",
            {
                **enriched_ctx,
                "agent_outputs": final_responses,
                "final_confidence": current_confidence,
                "debate_rounds": self.debate_rounds
            }
        )

        print(f"[ORCHESTRATOR V5] ✅ Terminé après {self.debate_rounds} rounds → confiance {current_confidence:.2f}")
        return final_responses, final

    def _enrich_context(self, context: dict) -> dict:
        """Ajoute toujours le glossaire et les règles communes."""
        return {**context, "shared_glossary": self.kb.get_glossary()}

    def _check_for_crash_flag(self):
        return None

    async def run(self, market_data: dict, memory: dict) -> Dict[str, Any]:
        symbol = market_data.get("symbol", "UNKNOWN")

        context = {
            "market_data": market_data,
            "memory": memory,
            "symbol": symbol,
            "shared_glossary": self.kb.get_glossary()
        }

        responses, final = await self.ask_all(
            f"Analyse complète du marché {symbol} et propose une décision de trade",
            context
        )

        return {
            "responses": responses,
            "final_decision": final,
            "debate_rounds": self.debate_rounds,
            "status": "ok"
        }
