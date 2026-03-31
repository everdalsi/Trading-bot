"""
🎯 ORCHESTRATOR V5.2 — Cerveau Collectif Parfait + Zéro Malentendu + Spécialisation Forcée
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX V5.2 :
- final_responses initialisé avant la boucle (NameError impossible)
- PortfolioManager instancié une seule fois (plus de doublon module-level)
- SelfImprovementAgent importé depuis self_improvement (agent correcte)
- Timeout global debug message plus précis
"""

import asyncio
from typing import Dict, Any, List, Tuple
import os

from agents.base_agent import BaseAgent
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
# FIX V5.2 : import du bon agent SelfImprovement (plus le copié-collé de LearningAgent)
from agents.self_improvement import SelfImprovementAgent
from agents.wallet_copier_agent import WalletCopierAgent
from agents.social_listener_agent import SocialListenerAgent

# === UPGRADE V5 : NOUVEAUX AGENTS INTÉGRÉS AU CERVEAU COLLECTIF ===
from agents.quant_ml_agent import QuantMLAgent
from agents.execution_engine_agent import ExecutionEngineAgent
from agents.yield_staking_agent import YieldStakingAgent
from agents.hedging_agent import HedgingAgent

# ======================== PATCH ORCHESTRATOR V5.2 ========================
from agents.base_agent import _KnowledgeBaseSingleton
# ===========================================================================


class Orchestrator:

    def __init__(self):
        # ======================== PATCH SINGLETON KB ========================
        self.kb = _KnowledgeBaseSingleton.get_instance()
        # ====================================================================

        self.analyst              = AnalystAgent()
        self.risk                 = RiskAgent()
        self.trader               = TraderAgent()
        self.supervisor           = SupervisorAgent()
        self.learning             = LearningAgent()
        self.performance          = PerformanceTracker()
        self.research             = ResearchAgent()
        self.knowledge_specialist = KnowledgeSpecialistAgent()
        self.evolution            = EvolutionAgent(orchestrator=self)
        # FIX V5.2 : self_improvement utilise maintenant le bon agent (SelfImprovementAgent)
        self.self_improvement     = SelfImprovementAgent(orchestrator=self)
        self.wallet_copier        = WalletCopierAgent()
        # FIX V5.2 : PortfolioManager instancié une seule fois ici (supprimé au niveau module)
        self.portfolio_manager    = PortfolioManager()
        self.social_listener      = SocialListenerAgent()

        self.quant_ml           = QuantMLAgent()
        self.execution_engine   = ExecutionEngineAgent()
        self.yield_staking      = YieldStakingAgent()
        self.hedging            = HedgingAgent()

        self.debate_rounds = 0

    async def ask_all(
        self, question: str, context: dict
    ) -> Tuple[List[Dict], Dict]:
        print(f"[ORCHESTRATOR V5.2] 🚀 Analyse collective → {question[:80]}...")

        # === Glossaire commun forcé pour zéro malentendu ===
        context["shared_glossary"] = self.kb.get_glossary()

        # === Vérification santé système ===
        immune_status = await self.self_improvement.safe_respond("monitor health", context)
        context["immune_health"] = immune_status.get("score", 100)

        restart_reason = self._check_for_crash_flag()
        if restart_reason:
            context["restart_reason"] = restart_reason

        enriched_ctx = self._enrich_context(context)

        # === PHASE 1 : Appel parallèle avec safe_respond + TIMEOUT ===
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
            self.quant_ml.safe_respond(question, enriched_ctx),
            self.execution_engine.safe_respond(question, enriched_ctx),
            self.yield_staking.safe_respond(question, enriched_ctx),
            self.hedging.safe_respond(question, enriched_ctx),
            self.portfolio_manager.safe_respond(question, enriched_ctx),
        ]

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=15.0
            )
        except asyncio.TimeoutError:
            print("[ORCHESTRATOR V5.2] ⚠️ Timeout global Phase 1 → fallback NO TRADE")
            return [], {"decision": "NO TRADE", "reason": "Timeout Orchestrator Phase 1", "score": 0.0}

        responses = []
        agent_names = [
            "analyst", "risk", "trader", "learning", "research",
            "knowledge_specialist", "wallet_copier", "social_listener",
            "self_improvement", "quant_ml", "execution_engine",
            "yield_staking", "hedging", "portfolio_manager"
        ]

        q_lower = question.lower()
        is_debate = any(kw in q_lower for kw in [
            "débat", "synthétise", "synthèse", "collective", "final decision",
            "verdict", "trade ou no trade", "analyse micro", "décision finale",
            "cerveau collectif", "orchestrator", "ask_all", "round", "micro", "meme"
        ])

        for i, res in enumerate(results):
            if isinstance(res, Exception):
                responses.append({
                    "agent": agent_names[i] if i < len(agent_names) else f"agent_{i}",
                    "summary": f"Exception interne: {str(res)[:100]}",
                    "confidence": 0.0,
                    "recommendation": "Vérifier rôle",
                })
            else:
                if res.get("warning") and not is_debate:
                    print(f"[ORCHESTRATOR V5.2] ⚠️ {res.get('agent', 'unknown')} hors domaine → ignoré")
                    continue
                responses.append(res)

        # FIX V5.2 : final_responses initialisé AVANT la boucle pour éviter NameError
        # si la boucle while fait un break avant d'assigner final_responses
        final_responses: List[Dict] = list(responses)

        # === PHASE DÉBAT COLLECTIF ===
        current_confidence = 0.0
        max_rounds = 7
        self.debate_rounds = 0

        while current_confidence < 0.99 and self.debate_rounds < max_rounds:
            self.debate_rounds += 1
            print(
                f"[ORCHESTRATOR V5.2] 🔥 Débat round {self.debate_rounds}/{max_rounds} "
                f"— confiance {current_confidence:.2f}"
            )

            collaboration_ctx = {
                **enriched_ctx,
                "agent_outputs":   responses,
                "previous_round":  responses,
                "debate_round":    self.debate_rounds,
                "target_confidence": 0.99,
                "strict_veto_mode": True,
                "shared_glossary":  self.kb.get_glossary(),
            }

            collab_tasks = [
                self.analyst.safe_respond("Raffine ton analyse (glossaire commun) et vise ≥ 99%", collaboration_ctx),
                self.risk.safe_respond("Raffine ton analyse (glossaire commun) et vise ≥ 99%", collaboration_ctx),
                self.trader.safe_respond("Raffine ton analyse (glossaire commun) et vise ≥ 99%", collaboration_ctx),
                self.learning.safe_respond("Raffine ton analyse (glossaire commun) et vise ≥ 99%", collaboration_ctx),
                self.research.safe_respond("Raffine ton analyse (glossaire commun) et vise ≥ 99%", collaboration_ctx),
                self.knowledge_specialist.safe_respond("Raffine ton analyse (glossaire commun) et vise ≥ 99%", collaboration_ctx),
                self.wallet_copier.safe_respond("Raffine ton analyse (glossaire commun) et vise ≥ 99%", collaboration_ctx),
                self.social_listener.safe_respond("Raffine ton analyse (glossaire commun) et vise ≥ 99%", collaboration_ctx),
                self.self_improvement.safe_respond("Raffine ton analyse (glossaire commun) et vise ≥ 99%", collaboration_ctx),
                self.quant_ml.safe_respond("Raffine ton analyse (glossaire commun) et vise ≥ 99%", collaboration_ctx),
                self.execution_engine.safe_respond("Raffine ton analyse (glossaire commun) et vise ≥ 99%", collaboration_ctx),
                self.yield_staking.safe_respond("Raffine ton analyse (glossaire commun) et vise ≥ 99%", collaboration_ctx),
                self.hedging.safe_respond("Raffine ton analyse (glossaire commun) et vise ≥ 99%", collaboration_ctx),
                self.portfolio_manager.safe_respond("Raffine ton analyse (glossaire commun) et vise ≥ 99%", collaboration_ctx),
            ]

            try:
                collab_results = await asyncio.wait_for(
                    asyncio.gather(*collab_tasks, return_exceptions=True),
                    timeout=12.0
                )
            except asyncio.TimeoutError:
                print(
                    f"[ORCHESTRATOR V5.2] ⚠️ Timeout round {self.debate_rounds} "
                    "→ sortie avec décision actuelle"
                )
                break

            # Veto dur Risk / Learning
            risk_resp = next(
                (r for r in collab_results if isinstance(r, dict) and r.get("agent") == "risk"), {}
            )
            learn_resp = next(
                (r for r in collab_results if isinstance(r, dict) and r.get("agent") == "learning"), {}
            )

            if (
                risk_resp.get("risk_level") in ["CRITICAL", "HIGH"]
                or "STOP" in str(risk_resp.get("recommendation", "")).upper()
            ):
                return responses, {"decision": "NO TRADE", "reason": "VETO RISK TOTAL", "score": 0.0}

            if learn_resp.get("blacklist", False):
                return responses, {"decision": "NO TRADE", "reason": "VETO LEARNING", "score": 0.0}

            # FIX V5.2 : mise à jour de final_responses à chaque round réussi
            final_responses = [r for r in collab_results if not isinstance(r, Exception)]
            current_confidence = max(
                (r.get("confidence", 0) for r in final_responses), default=0.0
            )

        # Décision finale via Supervisor
        final = await self.supervisor.respond(
            "Synthétise tout avec le glossaire commun et donne décision finale claire : TRADE ou NO TRADE",
            {
                **enriched_ctx,
                "agent_outputs":    final_responses,
                "final_confidence": current_confidence,
                "debate_rounds":    self.debate_rounds,
            }
        )

        print(
            f"[ORCHESTRATOR V5.2] ✅ Terminé après {self.debate_rounds} rounds "
            f"→ confiance {current_confidence:.2f}"
        )
        return final_responses, final

    def _enrich_context(self, context: dict) -> dict:
        """Ajoute toujours le glossaire et les règles communes."""
        return {**context, "shared_glossary": self.kb.get_glossary()}

    def _check_for_crash_flag(self):
        """Détecte un flag de crash lors du démarrage précédent."""
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
            "market_data":    market_data,
            "memory":         memory,
            "symbol":         symbol,
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
