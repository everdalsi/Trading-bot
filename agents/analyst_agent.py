from agents.base_agent import BaseAgent

class AnalystAgent(BaseAgent):
    def __init__(self):
        super().__init__("analyst", "Analyse performance et stats")

    async def respond(self, question, context):
        sim = context.get("sim", {})

        trades = [t for t in sim.get("trades", []) if isinstance(t.get("pnl"), (int, float))]
        valid_trades = [t for t in trades if t.get("pnl") is not None]

        wins = [t for t in trades if t["pnl"] > 0]

        wr = (len(wins) / max(len(trades), 1)) * 100

        return {
            "agent": self.name,
            "summary": f"WR actuel estimé à {wr:.1f}%",
            "arguments": [
                f"{len(valid_trades)} trades analysés",
                f"{len(wins)} trades gagnants"
            ],
            "risks": [
                "échantillon faible si peu de trades récents"
            ],
            "confidence": 0.7,
            "recommendation": "Continuer à monitor le WR sur 30 trades"
        }
