from agents.base_agent import BaseAgent

class RiskAgent(BaseAgent):
    def __init__(self):
        super().__init__("risk", "Gestion du risque")

    async def respond(self, question, context):
        kelly = context.get("kelly", 0.22)
        drawdown = context.get("drawdown", 0)

        risk_flag = "OK"

        if kelly > 0.25:
            risk_flag = "HIGH RISK"

        return {
            "agent": self.name,
            "summary": f"Kelly: {kelly*100:.1f}%",
            "arguments": [
                "Kelly élevé = agressif" if kelly > 0.2 else "Kelly raisonnable",
                f"Drawdown actuel: {drawdown}"
            ],
            "risks": [
                "drawdown rapide",
                "sur-exposition"
            ],
            "confidence": 0.8,
            "recommendation": "Réduire Kelly à 15%" if kelly > 0.25 else "Kelly OK"
        }
