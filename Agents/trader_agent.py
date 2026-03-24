from agents.base_agent import BaseAgent

class TraderAgent(BaseAgent):
    def __init__(self):
        super().__init__("trader", "Décision trading")

    async def respond(self, question, context):
        macro = context.get("macro", "neutral")

        return {
            "agent": self.name,
            "summary": f"Marché {macro}",
            "arguments": [
                "macro neutre = prudence",
                "pas de signal fort détecté"
            ],
            "risks": [
                "overtrading",
                "fake signals"
            ],
            "confidence": 0.6,
            "recommendation": "Attendre confirmation avant trade"
        }
