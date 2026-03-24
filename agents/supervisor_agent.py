“””
👑 SUPERVISOR AGENT V3 — Décision finale robuste + insights mémoire
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Améliorations vs V2 :

- Intègre les insights de la mémoire infinie dans la décision
- Score composite (trader + risk + learning + patterns)
- Veto sur dégradation de performance détectée
- Explications plus détaillées pour le mode secrétaire
  “””

from agents.base_agent import BaseAgent
from typing import Dict, Any

class SupervisorAgent(BaseAgent):

```
def __init__(self):
    super().__init__(
        name="supervisor",
        role="Synthèse finale, arbitrage et décision ultime"
    )

async def respond(self, question: str, context: dict) -> Dict[str, Any]:
    agent_outputs    = context.get("agent_outputs", [])
    trader_decision  = context.get("trader_decision", {})
    risk             = context.get("risk", {})
    score            = context.get("score", 0.5)
    symbol           = context.get("symbol", "UNKNOWN")

    # Données mémoire infinie
    lesson_count  = context.get("lesson_count", 0)
    global_score  = context.get("global_score", score)
    insights      = context.get("insights", [])
    auto_rules    = context.get("auto_rules", [])
    degraded      = context.get("degraded", False)
    streak_type   = context.get("streak_type", "neutral")
    streak_count  = context.get("streak_count", 0)

    # Signaux trader et risk
    trader_summary = str(trader_decision.get("summary", "")).upper()
    trader_decision_val = str(trader_decision.get("decision", "HOLD")).upper()
    risk_summary   = str(risk.get("summary", "")).upper()
    risk_reco      = str(risk.get("recommendation", ""))

    has_buy  = "BUY" in trader_summary or trader_decision_val == "BUY"
    has_sell = "SELL" in trader_summary or trader_decision_val == "SELL"

    final_decision = "HOLD"
    reason         = "Pas de consensus clair"

    # ─── Veto 1 : Risque critique ───
    if "CRITICAL" in risk_summary or "STOP" in risk_reco:
        final_decision = "NO TRADE"
        reason = "Veto risque critique — capital à protéger"

    # ─── Veto 2 : Performance dégradée ───
    elif degraded:
        final_decision = "NO TRADE"
        reason = "Performance en dégradation détectée — pause prudente"

    # ─── Veto 3 : Streak de pertes longue ───
    elif streak_type == "loss" and streak_count >= 5:
        final_decision = "NO TRADE"
        reason = f"Série de {streak_count} pertes consécutives — pause obligatoire"

    # ─── Signal BUY ───
    elif has_buy and not has_sell:
        effective_score = (score + global_score) / 2
        if (effective_score >= 0.60
                and "HIGH RISK" not in risk_reco
                and "STOP" not in risk_reco):
            final_decision = "BUY"
            reason = (
                f"Confluence haussière | score={effective_score:.2f} "
                f"| mémoire={lesson_count}leçons"
            )
        else:
            final_decision = "HOLD"
            reason = f"Signal BUY insuffisant (score={score:.2f} < 0.60) ou risque trop élevé"

    # ─── Signal SELL ───
    elif has_sell and not has_buy:
        if score <= 0.40:
            final_decision = "SELL"
            reason = "Confluence baissière confirmée"
        else:
            final_decision = "HOLD"
            reason = "Signal SELL faible — pas assez de confluence"

    # ─── Boost par auto-règles ───
    if final_decision == "HOLD" and auto_rules and score >= 0.58:
        # Si des règles automatiques valident le setup, on reconsidère
        buy_rules = [r for r in auto_rules if "✅" in r]
        if len(buy_rules) >= 2:
            final_decision = "BUY"
            reason = f"Règles automatiques validées ({len(buy_rules)} règles actives)"

    # Synthèse des agents pour le mode secrétaire
    agent_lines = []
    for out in agent_outputs[:4]:
        agent_lines.append(
            f"• {out.get('agent', '').title()}: {out.get('summary', '')[:80]}"
        )

    # Insights pertinents
    insight_str = ""
    if insights:
        insight_str = " | Insight: " + insights[0][:60]

    confidence_final = round(max(0.50, min(0.95, (score + global_score) / 2 * 0.9)), 2)

    return {
        "agent": self.name,
        "decision": final_decision,
        "summary": (
            f"DÉCISION → {final_decision} | "
            f"Score: {score:.2f} | "
            f"Mémoire: {lesson_count}∞{insight_str}"
        ),
        "arguments": [
            f"Symbol: {symbol}",
            f"Score composite: {(score + global_score) / 2:.2f}",
            f"Trader: {trader_decision_val} | Risk: {risk_summary[:50]}",
            f"Leçons en mémoire: {lesson_count}",
            f"Règles auto actives: {len(auto_rules)}",
        ],
        "risks": (
            [] if final_decision in ("BUY", "SELL") else
            ["Décision bloquée — voir raison ci-dessus"]
        ),
        "confidence": confidence_final,
        "recommendation": reason,
        "full_summary": "\n".join(agent_lines),
        "final_decision": final_decision,
    }
```
