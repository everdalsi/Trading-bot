from agents.base_agent import BaseAgent

class AnalystAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="analyst",
            description="Analyse performance, winrate, stats et historique des trades"
        )

    async def respond(self, question: str, context: dict):
        # === PRIORITÉ 1 : Données live du PerformanceTracker ===
        wr_live = context.get("wr_live")
        wins_live = context.get("wins_live")
        losses_live = context.get("losses_live")
        total_live = context.get("total_trades")

        if isinstance(wr_live, (int, float)):
            total = total_live if isinstance(total_live, int) else (wins_live or 0) + (losses_live or 0)
            return {
                "agent": self.name,
                "summary": f"WR actuel estimé à {wr_live:.1f}%",
                "arguments": [
                    f"{total} trades analysés (live)",
                    f"{wins_live or 0} gagnants | {losses_live or 0} perdants",
                    f"Source: PerformanceTracker"
                ],
                "risks": ["Données live utilisées – très fiable"],
                "confidence": 0.95,
                "recommendation": "Le bot est en phase d'apprentissage. Continuer à monitorer sur 30+ trades."
            }

        # === PRIORITÉ 2 : Fallback sur le JSON sim ===
        sim = context.get("sim", {})
        trades = [
            t for t in sim.get("trades", [])
            if isinstance(t.get("pnl"), (int, float))
        ]
        wins = [t for t in trades if t["pnl"] > 0]
        total = len(trades)
        wr = round((len(wins) / total * 100), 1) if total > 0 else 0.0

        # Analyse rapide de la question pour personnaliser un peu
        q_lower = question.lower()
        if any(x in q_lower for x in ["winrate", "wr", "performance", "stat"]):
            extra = f" (question portait sur le winrate)"
        elif "risque" in q_lower or "kelly" in q_lower:
            extra = f" (question portait sur le risque)"
        else:
            extra = ""

        return {
            "agent": self.name,
            "summary": f"WR actuel estimé à {wr:.1f}%",
            "arguments": [
                f"{total} trades analysés{extra}",
                f"{len(wins)} trades gagnants",
                f"Données extraites du portfolio JSON"
            ],
            "risks": ["Fallback JSON utilisé – données moins fraîches"],
            "confidence": 0.75 if total >= 10 else 0.45,
            "recommendation": "Peu de trades pour l'instant. Le vrai WR se stabilisera après 30-50 trades."
        }
