from agents.base_agent import BaseAgent

class AnalystAgent(BaseAgent):

    def __init__(self):
        super().__init__("analyst", "Analyse performance et stats")

    async def respond(self, question, context):
        wr_live = context.get("wr_live")
        wins_live = context.get("wins_live")
        losses_live = context.get("losses_live")

        if isinstance(wr_live, (int, float)):
            total = 0
            if isinstance(wins_live, int) and isinstance(losses_live, int):
                total = wins_live + losses_live

            return {
                "agent": self.name,
                "summary": f"WR actuel estimé à {wr_live:.1f}%",
                "arguments": [
                    f"{total} trades analysés" if total > 0 else "WR récupéré depuis le bot",
                    f"{wins_live} trades gagnants" if isinstance(wins_live, int) else "Wins non disponibles"
                ],
                "risks": [
                    "source live prioritaire"
                ],
                "confidence": 0.9,
                "recommendation": "Continuer à monitor le WR réel"
            }

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
                "fallback JSON utilisé"
            ],
            "confidence": 0.7,
            "recommendation": "Continuer à monitor le WR sur 30 trades"
        }
