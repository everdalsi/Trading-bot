from agents.analyst_agent import AnalystAgent
from agents.risk_agent import RiskAgent
from agents.trader_agent import TraderAgent
from agents.supervisor_agent import SupervisorAgent
from agents.learning_agent import adjust_confidence, should_blacklist
from agents.performance_tracker import PerformanceTracker


class Orchestrator:
    def __init__(self):
        self.analyst = AnalystAgent()
        self.risk = RiskAgent()
        self.trader = TraderAgent()
        self.supervisor = SupervisorAgent()
        self.performance = PerformanceTracker()

    async def run(self, market_data, memory):

        symbol = market_data.get("symbol", "UNKNOWN")

        context = {
            "symbol": symbol,
            "market_data": market_data,
            "memory": memory
        }

        try:
            if should_blacklist(memory.data, symbol):
                return {"decision": "NO TRADE", "reason": "learning blacklist"}
        except Exception:
            pass

        try:
            analysis = await self.analyst.respond("analyze", context)
            context["analysis"] = analysis or {}

            risk = await self.risk.respond("assess_risk", context)
            context["risk"] = risk or {}

            try:
                symbol_score = memory.get_symbol_score(symbol)
            except Exception:
                symbol_score = 0.5

            context["symbol_score"] = symbol_score

            base_score = self.compute_score(context["analysis"], context["risk"])
            score = (base_score * 0.8) + (symbol_score * 0.2)
            score = adjust_confidence(score, memory.data, symbol)
            score = max(0, min(score, 1))

            context["score"] = score

            decision = await self.trader.respond("decide", context)
            context["decision"] = decision or {}

            final = await self.supervisor.respond("validate", context)

            if not final:
                return {"decision": "NO TRADE", "reason": "no supervisor output"}

            try:
                final["position_size"] = self.get_position_size(
                    balance=1000,
                    risk_per_trade=0.02,
                    confidence=score
                )
            except Exception:
                final["position_size"] = 0

            decision_value = final.get("decision")

            if decision_value in ["BUY", "SELL"]:
                try:
                    memory.add_trade({
                        "symbol": symbol,
                        "decision": decision_value,
                        "confidence": score,
                        "result": "pending"
                    })
                except Exception:
                    pass

            try:
                self.performance.log_trade({
                    "symbol": symbol,
                    "decision": decision_value,
                    "confidence": score
                })
            except Exception:
                pass

            print(f"""
            ===== TRADE DEBUG =====
            Symbol: {symbol}
            Base Score: {base_score:.2f}
            Symbol Score: {symbol_score:.2f}
            Final Score: {score:.2f}
            Decision: {decision_value}
            Position Size: {final.get("position_size")}
            ======================
            """)

            return final

        except Exception as e:
            return {"decision": "ERROR", "error": str(e)}

    def compute_score(self, analysis, risk):
        try:
            trend = analysis.get("trend_score", 0.5)
            pattern = analysis.get("pattern_score", 0.5)
            volume = analysis.get("volume_score", 0.5)
            risk_score = risk.get("risk_score", 0.5)

            score = (
                trend * 0.4 +
                pattern * 0.3 +
                volume * 0.2 +
                (1 - risk_score) * 0.1
            )

            return max(0, min(score, 1))
        except Exception:
            return 0.5

    def get_position_size(self, balance, risk_per_trade, confidence):
        try:
            base_risk = balance * risk_per_trade
            adjusted = base_risk * confidence
            return round(adjusted, 2)
        except Exception:
            return 0
