from agents.analyst_agent import AnalystAgent
from agents.risk_agent import RiskAgent
from agents.trader_agent import TraderAgent
from agents.supervisor_agent import SupervisorAgent


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
        try:
            if memory.is_bad_symbol(symbol):
                print(f"⛔ Skipping bad symbol: {symbol}")
                return {"decision": "NO TRADE", "reason": "blacklisted"}
        except Exception as e:
            print(f"⚠️ Memory blacklist check error: {e}")

        try:
            # 1. ANALYST 🧠
            analysis = await self.analyst.respond("analyze", context)
            context["analysis"] = analysis or {}

            # 2. RISK 📉
            risk = await self.risk.respond("assess_risk", context)
            context["risk"] = risk or {}

            # 🔥 SYMBOL SCORE (learning par coin)
            try:
                symbol_score = memory.get_symbol_score(symbol)
            except Exception:
                symbol_score = 0.5  # fallback safe

            context["symbol_score"] = symbol_score

            # 3. SCORE 🔥 (fusion intelligence)
            base_score = self.compute_score(context["analysis"], context["risk"])

            # 🔥 boost avec historique
            score = (base_score * 0.8) + (symbol_score * 0.2)
            score = max(0, min(score, 1))  # clamp sécurité

            context["score"] = score

            # 4. TRADER 💰
            decision = await self.trader.respond("decide", context)
            context["decision"] = decision or {}

            # 5. SUPERVISOR 🧠
            final = await self.supervisor.respond("validate", context)

            # ❗ sécurité si supervisor fail
            if not final:
                return {"decision": "NO TRADE", "reason": "no supervisor output"}

            # 💰 POSITION SIZING
            try:
                final["position_size"] = self.get_position_size(
                    balance=1000,  # 👉 à connecter à ton wallet plus tard
                    risk_per_trade=0.02,
                    confidence=score
                )
            except Exception as e:
                print(f"⚠️ Position sizing error: {e}")
                final["position_size"] = 0

            # 🧠 MEMORY LOG (safe)
            decision_value = final.get("decision")

            if decision_value in ["BUY", "SELL"]:
                try:
                    memory.add_trade({
                        "symbol": symbol,
                        "decision": decision_value,
                        "confidence": score,
                        "result": "pending"
                    })
                except Exception as e:
                    print(f"⚠️ Memory add_trade error: {e}")

            # 🔍 DEBUG (propre)
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

            return max(0, min(score, 1))

        except Exception as e:
            print(f"⚠️ Score computation error: {e}")
            return 0.5

    # 💰 POSITION SIZING
    def get_position_size(self, balance, risk_per_trade, confidence):
        try:
            base_risk = balance * risk_per_trade
            adjusted = base_risk * confidence
            return round(adjusted, 2)
        except Exception as e:
            print(f"⚠️ Position size error: {e}")
            return 0
