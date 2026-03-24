from agents.analyst import AnalystAgent
from agents.risk import RiskAgent
from agents.trader import TraderAgent
from agents.supervisor import SupervisorAgent


class Orchestrator:
    def __init__(self):
        self.analyst = AnalystAgent()
        self.risk = RiskAgent()
        self.trader = TraderAgent()
        self.supervisor = SupervisorAgent()

    async def run(self, market_data, memory):

        # 🔒 SAFE CONTEXT
        symbol = market_data.get("symbol", "UNKNOWN")

        context = {
            "symbol": symbol,
            "market_data": market_data,
            "memory": memory
        }

        # 💀 BLACKLIST CHECK
        if memory.is_bad_symbol(symbol):
            print(f"⛔ Skipping bad symbol: {symbol}")
            return {"decision": "NO TRADE", "reason": "blacklisted"}

        try:
            # 1. ANALYST 🧠
            analysis = await self.analyst.respond("analyze", context)
            context["analysis"] = analysis

            # 2. RISK 📉
            risk = await self.risk.respond("assess_risk", context)
            context["risk"] = risk

            # 3. SCORE 🔥 (fusion intelligence)
            score = self.compute_score(analysis, risk)
            context["score"] = score

            # 4. TRADER 💰
            decision = await self.trader.respond("decide", context)
            context["decision"] = decision

            # 5. SUPERVISOR 🧠
            final = await self.supervisor.respond("validate", context)

            # 💰 POSITION SIZING
            final["position_size"] = self.get_position_size(
                balance=1000,  # 👉 à connecter à ton wallet plus tard
                risk_per_trade=0.02,
                confidence=score
            )

            # 🧠 MEMORY LOG
            if final["decision"] in ["BUY", "SELL"]:
                memory.add_trade({
                    "symbol": symbol,
                    "decision": final["decision"],
                    "confidence": score,
                    "result": "pending"
                })

            # 🔍 DEBUG
            print(f"""
            ===== TRADE DEBUG =====
            Symbol: {symbol}
            Score: {score:.2f}
            Decision: {final.get("decision")}
            Position Size: {final.get("position_size")}
            ======================
            """)

            return final

        except Exception as e:
            print(f"🔥 ERROR in orchestrator: {e}")
            return {"decision": "ERROR", "error": str(e)}

    # 🔥 SCORE ENGINE
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

            return max(0, min(score, 1))  # clamp 0-1

        except:
            return 0.5

    # 💰 POSITION SIZING
    def get_position_size(self, balance, risk_per_trade, confidence):
        base_risk = balance * risk_per_trade
        adjusted = base_risk * confidence
        return round(adjusted, 2)
