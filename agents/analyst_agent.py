from agents.base_agent import BaseAgent

class AnalystAgent(BaseAgent):
    def __init__(self):
        super().__init__("analyst", "Analyse performance et stats")

    async def respond(self, question, context):
        sim = context.get("sim", {})

trades = [
    t for t in sim.get("trades", [])
    if isinstance(t.get("pnl"), (int, float))
]

wins = [t for t in trades if t["pnl"] > 0]

total = len(trades)
wr = (len(wins) / total) * 100 if total > 0 else 0

        return {
            "agent": self.name,
            "summary": f"WR actuel estimé à {wr:.1f}%",
            "arguments": [
                f"{total} trades analysés",
                f"{len(wins)} trades gagnants"
            ],
            "risks": [
                "échantillon faible si peu de trades récents"
            ],
            "confidence": 0.7,
            "recommendation": "Continuer à monitor le WR sur 30 trades"
        }
