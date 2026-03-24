from agents.analyst_agent import AnalystAgent
from agents.risk_agent import RiskAgent
from agents.trader_agent import TraderAgent
from agents.supervisor_agent import SupervisorAgent
from agents.learning_agent import LearningAgent
from agents.performance_tracker import PerformanceTracker
from typing import Dict, Any

class Orchestrator:
    def __init__(self):
        self.analyst = AnalystAgent()
        self.risk = RiskAgent()
        self.trader = TraderAgent()
        self.supervisor = SupervisorAgent()
        self.learning = LearningAgent()          # ← nouvel agent intégré
        self.performance = PerformanceTracker()

    async def run(self, market_data: dict, memory: dict) -> Dict[str, Any]:
        symbol = market_data.get("symbol", "UNKNOWN")

        context = {
            "symbol": symbol,
            "market_data": market_data,
            "memory": memory,
            "sim": memory.get("sim", {}),           # pour compatibilité
            "base_confidence": 0.65
        }

        # 1. BLACKLIST via Learning Agent
        blacklist_check = await self.learning.respond("should I blacklist this symbol?", context)
        if blacklist_check.get("recommendation", "").lower().startswith("éviter"):
            return {"decision": "NO TRADE", "reason": "learning_blacklist", "score": 0.0}

        try:
            # 2. Analyse parallèle des agents
            analysis = await self.analyst.respond("analyze current market", context)
            risk     = await self.risk.respond("assess risk", context)
            trader_decision = await self.trader.respond("make trading decision", context)

            context.update({
                "analysis": analysis,
                "risk": risk,
                "trader_decision": trader_decision
            })

            # 3. Score final avec Learning Agent
            learning_result = await self.learning.respond("compute global and symbol score", context)
            final_score = learning_result.get("confidence", 0.5)

            context["score"] = final_score

            # 4. Validation finale par Supervisor
            final = await self.supervisor.respond("validate final decision", context)

            if not final or final.get("decision") == "NO TRADE":
                return {"decision": "NO TRADE", "reason": final.get("reason", "supervisor_rejected"), "score": final_score}

            # 5. Position sizing
            position_size = self.get_position_size(
                balance=1000,
                risk_per_trade=0.02,
                confidence=final_score
            )
            final["position_size"] = position_size

            # 6. Log dans memory + performance
            if final.get("decision") in ["BUY", "SELL"]:
                try:
                    memory.setdefault("trades", []).append({
                        "symbol": symbol,
                        "decision": final["decision"],
                        "confidence": final_score,
                        "result": "pending",
                        "timestamp": "now"
                    })
                except Exception:
                    pass

                try:
                    self.performance.log_trade({
                        "symbol": symbol,
                        "decision": final["decision"],
                        "confidence": final_score
                    })
                except Exception:
                    pass

            # Debug clair
            print(f"""
            ===== ORCHESTRATOR DECISION =====
            Symbol     : {symbol}
            Score final: {final_score:.3f}
            Decision   : {final.get('decision')}
            Position   : ${position_size}
            Reason     : {final.get('reason', 'N/A')}
            =================================
            """)

            return final

        except Exception as e:
            print(f"[ORCHESTRATOR ERROR] {e}")
            return {"decision": "ERROR", "reason": str(e)[:100], "score": 0.0}

    def get_position_size(self, balance: float, risk_per_trade: float, confidence: float) -> float:
        try:
            base_risk = balance * risk_per_trade
            adjusted = base_risk * max(0.3, confidence)   # minimum 30% de la taille même en faible confiance
            return round(adjusted, 2)
        except Exception:
            return 0.0
