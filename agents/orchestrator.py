"""
🎯 ORCHESTRATOR V4 — Cerveau Collectif Parfait + Zéro Malentendu + Spécialisation Forcée
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Upgrade BaseAgent V3 + glossaire partagé + safe_respond partout + vérif rôle stricte
"""

import asyncio
from typing import Dict, Any, List, Tuple
import os

from agents.base_agent import BaseAgent  # ← UPGRADE : base commune avec safe_respond
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

class Orchestrator:

    def __init__(self):
        self.kb = KnowledgeBase()  # ← UPGRADE : glossaire partagé accessible par TOUT le cerveau

        self.analyst    = AnalystAgent()
        self.risk       = RiskAgent()
        self.trader     = TraderAgent()
        self.supervisor = SupervisorAgent()
        self.learning   = LearningAgent()
        self.performance = PerformanceTracker()
        self.research   = ResearchAgent()
        self.knowledge_specialist = KnowledgeSpecialistAgent()
        self.self_improvement = SelfImprovementEngineer(orchestrator=self)
        self.wallet_copier = WalletCopierAgent()
        self.social_listener = SocialListenerAgent()
        self.debate_rounds = 0

    async def ask_all(
        self, question: str, context: dict
    ) -> Tuple[List[Dict], Dict]:
        print(f"[ORCHESTRATOR V4] Démarrage analyse → {question[:80]}...")

        # === UPGRADE : Glossaire commun forcé pour zéro malentendu ===
        context["shared_glossary"] = self.kb.get_glossary()

        # === UPGRADE : Vérification santé du système avec safe_respond ===
        immune_status = await self.self_improvement.safe_respond("monitor health", context)
        context["immune_health"] = immune_status.get("score", 100)

        restart_reason = self._check_for_crash_flag()
        if restart_reason:
            context["restart_reason"] = restart_reason

        enriched_ctx = self._enrich_context(context)

        # === PHASE 1 : Appel parallèle avec safe_respond (spécialisation stricte) ===
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
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        responses = []
        agent_names = ["analyst", "risk", "trader", "learning", "research", "knowledge_specialist", "wallet_copier", "social_listener", "self_improvement"]
        for i, res in enumerate(results):
            if isinstance(res, Exception) or "error" in str(res):
                responses.append({
                    "agent": agent_names[i],
                    "summary": f"Erreur interne: {str(res)[:100]}",
                    "confidence": 0.0,
                    "recommendation": "Vérifier rôle",
                })
            else:
                # Vérification spéciale : l’agent est-il dans son domaine ?
                if res.get("warning"):
                    print(f"[ORCHESTRATOR V4] ⚠️ {res['agent']} hors domaine → ignoré")
                    continue
                responses.append(res)

        # === PHASE DÉBAT COLLECTIF (jusqu’à ≥ 99 % + glossaire forcé à chaque round) ===
        current_confidence = 0.0
        max_rounds = 7
        self.debate_rounds = 0

        while current_confidence < 0.99 and self.debate_rounds < max_rounds:
            self.debate_rounds += 1
            print(f"[ORCHESTRATOR V4] Débat round {self.debate_rounds}/7 — confiance {current_confidence:.2f}")

            collaboration_ctx = {
                **enriched_ctx,
                "agent_outputs": responses,
                "previous_round": responses,
                "debate_round": self.debate_rounds,
                "target_confidence": 0.99,
                "strict_veto_mode": True,
                "shared_glossary": self.kb.get_glossary(),  # ← glossaire forcé
            }

            collab_tasks = [
                self.analyst.safe_respond(
                    f"Raffine ton analyse en tenant compte des autres agents (utilise le glossaire commun) et vise ≥ 99 %",
                    collaboration_ctx
                ),
                self.risk.safe_respond(
                    f"Raffine ton analyse en tenant compte des autres agents (utilise le glossaire commun) et vise ≥ 99 %",
                    collaboration_ctx
                ),
                self.trader.safe_respond(
                    f"Raffine ton analyse en tenant compte des autres agents (utilise le glossaire commun) et vise ≥ 99 %",
                    collaboration_ctx
                ),
                self.learning.safe_respond(
                    f"Raffine ton analyse en tenant compte des autres agents (utilise le glossaire commun) et vise ≥ 99 %",
                    collaboration_ctx
                ),
                self.research.safe_respond(
                    f"Raffine ton analyse en tenant compte des autres agents (utilise le glossaire commun) et vise ≥ 99 %",
                    collaboration_ctx
                ),
                self.knowledge_specialist.safe_respond(
                    f"Raffine ton analyse en tenant compte des autres agents (utilise le glossaire commun) et vise ≥ 99 %",
                    collaboration_ctx
                ),
                self.wallet_copier.safe_respond(
                    f"Raffine ton analyse en tenant compte des autres agents (utilise le glossaire commun) et vise ≥ 99 %",
                    collaboration_ctx
                ),
                self.social_listener.safe_respond(
                    f"Raffine ton analyse en tenant compte des autres agents (utilise le glossaire commun) et vise ≥ 99 %",
                    collaboration_ctx
                ),
            ]

            collab_results = await asyncio.gather(*collab_tasks, return_exceptions=True)

            # Filtrage + veto dur
            risk_resp = next((r for r in collab_results if isinstance(r, dict) and r.get("agent") == "risk"), {})
            learn_resp = next((r for r in collab_results if isinstance(r, dict) and r.get("agent") == "learning"), {})

            if risk_resp.get("risk_level") in ["CRITICAL", "HIGH"] or "STOP" in str(risk_resp.get("recommendation", "")).upper():
                return responses, {"decision": "NO TRADE", "reason": "VETO RISK TOTAL", "score": 0.0}

            if learn_resp.get("blacklist", False):
                return responses, {"decision": "NO TRADE", "reason": "VETO LEARNING", "score": 0.0}

            final_responses = [r for r in collab_results if not isinstance(r, Exception)]
            current_confidence = max((r.get("confidence", 0) for r in final_responses), default=0.0)

        # Décision finale via Supervisor
        final = await self.supervisor.respond("Synthétise tout avec le glossaire commun et donne décision finale", {
            **enriched_ctx,
            "agent_outputs": final_responses,
            "final_confidence": current_confidence,
            "debate_rounds": self.debate_rounds
        })

        print(f"[ORCHESTRATOR V4] Terminé après {self.debate_rounds} rounds → confiance {current_confidence:.2f}")
        return final_responses, final

    def _enrich_context(self, context: dict) -> dict:
        """Ajoute toujours le glossaire et les règles communes."""
        return {**context, "shared_glossary": self.kb.get_glossary()}

    def _check_for_crash_flag(self):
        """Méthode originale conservée"""
        return None  # placeholder si tu avais une implémentation avant

    async def run(self, market_data: dict, memory: dict) -> Dict[str, Any]:
        symbol = market_data.get("symbol", "UNKNOWN")

        context = {
            "market_data": market_data,
            "memory": memory,
            "symbol": symbol,
            "shared_glossary": self.kb.get_glossary()  # ← UPGRADE ajouté ici aussi
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
