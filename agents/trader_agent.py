from agents.base_agent import BaseAgent

class TraderAgent(BaseAgent):
    def __init__(self):
        super().__init__("trader", "Décision trading")

    async def respond(self, question, context):
        macro = context.get("macro", "neutral")
        memory = context.get("memory")

        # 🔥 1. DÉCISION SIMPLE
        if macro == "bullish":
            decision = "BUY"
            confidence = 0.7
        elif macro == "bearish":
            decision = "SELL"
            confidence = 0.7
        else:
            decision = "HOLD"
            confidence = 0.5

        # 🧠 2. ENREGISTRER LE TRADE
        if memory is not None:
            trade = {
                "decision": decision,
                "macro": macro,
                "confidence": confidence,
                "result": None  # sera rempli plus tard (win/loss)
            }

            memory["trades"].append(trade)

        # 📊 3. ARGUMENTS DYNAMIQUES
        arguments = []
        risks = []

        if macro == "bullish":
            arguments.append("tendance haussière détectée")
            risks.append("retournement soudain")
        elif macro == "bearish":
            arguments.append("tendance baissière détectée")
            risks.append("rebond technique")
        else:
            arguments.append("marché incertain")
            risks.append("faux signaux")

        # 📦 4. RETURN PROPRE
        return {
            "agent": self.name,
            "decision": decision,
            "summary": f"Marché {macro}",
            "arguments": arguments,
            "risks": risks,
            "confidence": confidence
        }
