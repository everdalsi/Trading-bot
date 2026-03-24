from agents.base_agent import BaseAgent
from typing import Dict, Any

class SupervisorAgent(BaseAgent):
    """
    SupervisorAgent V2 - Le juge final du système
    Analyse tous les outputs, résout les conflits, applique les veto risque
    et rend la décision ultime (BUY / SELL / HOLD / NO TRADE).
    """

    def __init__(self):
        super().__init__(
            name="supervisor",
            role="Synthèse finale, arbitrage et décision ultime"
        )

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        agent_outputs = context.get("agent_outputs", [])
        trader_decision = context.get("trader_decision", {})
        risk = context.get("risk", {})
        score = context.get("score", 0.5)
        symbol = context.get("symbol", "UNKNOWN")

        # Extraction des signaux clés
        trader_summary = trader_decision.get("summary", "").upper()
        risk_summary = risk.get("summary", "").upper()
        risk_reco = risk.get("recommendation", "")

        # Détection des signaux
        has_buy = "BUY" in trader_summary
        has_sell = "SELL" in trader_summary
        has_hold = "HOLD" in trader_summary or "NO TRADE" in trader_summary

        final_decision = "HOLD"
        reason = "Pas de consensus clair"

        # Logique de décision
        if has_buy and not has_sell and score >= 0.65:
            if "CRITICAL" not in risk_summary and "HIGH RISK" not in risk_reco and "STOP" not in risk_reco:
                final_decision = "BUY"
                reason = "Forte confluence haussière + risque acceptable"
            else:
                final_decision = "HOLD"
                reason = "Signal BUY bloqué par le Risk Agent"
        elif has_sell and not has_buy and score <= 0.35:
            final_decision = "SELL"
            reason = "Forte confluence baissière"
        elif "CRITICAL" in risk_summary or "STOP" in risk_reco:
            final_decision = "NO TRADE"
            reason = "Veto risque critique"

        # Synthèse lisible pour l'utilisateur
        agent_lines = [f"• {out.get('agent','')}: {out.get('summary','')[:80]}" for out in agent_outputs[:4]]

        return {
            "agent": self.name,
            "summary": f"DECISION FINALE → {final_decision} | Score global: {score:.2f}",
            "arguments": [
                f"Symbol: {symbol}",
                f"Trader: {trader_summary[:60]}",
                f"Risk: {risk_summary[:60]}",
                f"Score: {score:.2f}"
            ],
            "risks": [] if final_decision in ["BUY", "SELL"] else ["Décision bloquée par risque / manque de consensus"],
            "confidence": round(max(0.65, score * 0.9), 2),
            "recommendation": reason,
            "full_summary": "\n".join(agent_lines),
            "final_decision": final_decision   # ← très important pour l'orchestrator
        }
