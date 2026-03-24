from agents.base_agent import BaseAgent
from agents.learning_agent import adjust_confidence, compute_strategy_score


class TraderAgent(BaseAgent):
    def __init__(self):
        super().__init__("trader", "Décision trading")

    async def respond(self, question, context):
        macro = context.get("macro", "neutral")
        memory = context.get("memory", {})

        # 🔥 1. DÉCISION DE BASE (macro)
        if macro == "bullish":
            base_decision = "BUY"
            base_confidence = 0.7
        elif macro == "bearish":
            base_decision = "SELL"
            base_confidence = 0.7
        else:
            base_decision = "HOLD"
            base_confidence = 0.5

        # 🧠 2. LEARNING (ADAPTATION)
        confidence = adjust_confidence(base_confidence, memory)

        # 🔥 3. DÉCISION FINALE BASÉE SUR LEARNING
        if confidence > 0.7:
            decision = "BUY"
        elif confidence < 0.4:
            decision = "SELL"
        else:
            decision = "HOLD"

        # 🧠 4. ENREGISTRER LE TRADE
        if memory is not None:
            if "trades" not in memory:
                memory["trades"] = []

            trade = {
                "decision": decision,
                "macro": macro,
                "confidence": confidence,
                "entry_price": context.get("price"),
                "result": None
            }

            memory["trades"].append(trade)

        # 📊 5. ARGUMENTS DYNAMIQUES
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

        # 📊 6. SCORE LEARNING
        learning_score = compute_strategy_score(memory)

        # 📦 7. RETURN FINAL
        return {
            "agent": self.name,
            "decision": decision,
            "summary": f"Marché {macro}",
            "arguments": arguments,
            "risks": risks,
            "confidence": round(confidence, 2),
            "learning_score": round(learning_score, 2),
            "recommendation": f"{decision} basé sur performance historique"
        }
