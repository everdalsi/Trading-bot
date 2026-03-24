from agents.base_agent import BaseAgent
from typing import Dict, Any

class RiskAgent(BaseAgent):
    """
    RiskAgent V2 - Gestion du risque avancée
    Prend en compte Kelly, drawdown, daily loss, positions ouvertes, etc.
    """

    def __init__(self):
        super().__init__(
            name="risk",
            role="Gestion du risque avancée, drawdown, daily stop et position sizing"
        )

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        # Récupération des données du contexte
        kelly = context.get("kelly", 0.22)
        drawdown = context.get("drawdown", 0.0)
        daily_pnl_pct = context.get("daily_pnl_pct", 0.0)
        open_positions = context.get("open_positions", 0)
        max_positions = context.get("max_positions", 4)
        symbol = context.get("symbol")
        is_night = context.get("is_night", False)

        risk_level = "LOW"
        risks_list = []
        recommendation = "Risk acceptable - on peut trader"

        # 1. Kelly check
        if kelly > 0.28:
            risk_level = "VERY HIGH"
            risks_list.append(f"Kelly trop agressif ({kelly*100:.1f}%)")
        elif kelly > 0.22:
            risk_level = "HIGH"
            risks_list.append(f"Kelly élevé ({kelly*100:.1f}%)")

        # 2. Drawdown critique
        if drawdown <= -0.08:
            risk_level = "CRITICAL"
            risks_list.append(f"Drawdown dangereux ({drawdown*100:.1f}%)")
            recommendation = "STOP TRADING - Protéger le capital immédiatement"

        # 3. Daily loss
        if daily_pnl_pct <= -0.04:
            risks_list.append(f"Perte journalière importante ({daily_pnl_pct*100:.1f}%)")
            if risk_level != "CRITICAL":
                risk_level = "HIGH"

        # 4. Over-exposure
        if open_positions >= max_positions:
            risks_list.append(f"Nombre max de positions atteint ({open_positions}/{max_positions})")
            recommendation = "Attendre fermeture de positions"

        # 5. Mode nuit (réduction de risque)
        if is_night:
            risks_list.append("Mode nuit → risque réduit automatiquement")

        # Construction du résumé
        summary = f"Risk Level: {risk_level} | Kelly: {kelly*100:.1f}% | DD: {drawdown*100:.1f}%"

        return {
            "agent": self.name,
            "summary": summary,
            "arguments": [
                f"Positions ouvertes : {open_positions}/{max_positions}",
                f"Daily PnL : {daily_pnl_pct*100:.1f}%",
                f"Mode nuit : {'Actif' if is_night else 'Inactif'}"
            ],
            "risks": risks_list,
            "confidence": 0.88,
            "recommendation": recommendation
        }
