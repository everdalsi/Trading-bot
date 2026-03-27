from typing import Dict, Any
from agents.base_agent import BaseAgent

class SupervisorAgent(BaseAgent):

    def __init__(self):
        super().__init__(
            name="supervisor",
            role="Synthèse finale, arbitrage et décision ultime"
        )

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        # === EXTREME LEARNING MODE (MAX TRADES) ===
        extreme_learning = context.get("extreme_learning_mode", False) or context.get("learning_mode", False)

        agent_outputs    = context.get("agent_outputs", [])
        trader_decision  = context.get("trader_decision", {})
        risk             = context.get("risk", {})
        score            = context.get("score", 0.5)
        symbol           = context.get("symbol", "UNKNOWN")

        lesson_count  = context.get("lesson_count", 0)
        global_score  = context.get("global_score", score)
        insights      = context.get("insights", [])
        auto_rules    = context.get("auto_rules", [])
        degraded      = context.get("degraded", False)
        streak_type   = context.get("streak_type", "neutral")
        streak_count  = context.get("streak_count", 0)

        # === NOUVEAU : données réelles du portefeuille ===
        open_positions = len(context.get("memory", {}).get("positions", {})) if context.get("memory") else 0
        recent_trades  = context.get("memory", {}).get("trades", [])[-5:] if context.get("memory") else []

        trader_summary = str(trader_decision.get("summary", "")).upper()
        trader_decision_val = str(trader_decision.get("decision", "HOLD")).upper()
        risk_summary   = str(risk.get("summary", "")).upper()
        risk_reco      = str(risk.get("recommendation", ""))

        has_buy  = "BUY" in trader_summary or trader_decision_val == "BUY"
        has_sell = "SELL" in trader_summary or trader_decision_val == "SELL"

        learning_mode = any(word in question.lower() for word in [
            "max de trade", "apprenez", "affûtez", "apprendre", "max trade",
            "beaucoup de trades", "vrai argent", "vrai portefeuille", "gérer un vrai"
        ])

        # === MODE EXTREME LEARNING → ON MONTRE LES VRAIES DONNÉES + ON FORCE LES TRADES ===
        if learning_mode or extreme_learning:
            positions_str = f"{open_positions} positions ouvertes" if open_positions > 0 else "aucune position ouverte pour l’instant"
            lessons_str   = f"{lesson_count} leçons accumulées"
            last_trades   = "\n".join([f"• {t.get('symbol','?')} → {t.get('decision','BUY')}" for t in recent_trades]) or "aucun trade récent"

            return {
                "agent": self.name,
                "decision": "BUY",
                "summary": f"FORCE MAX TRADES — Apprentissage extrême activé | {positions_str} | {lessons_str}",
                "arguments": [
                    f"Positions ouvertes : {open_positions}",
                    f"Leçons en mémoire : {lesson_count}",
                    f"Derniers trades :\n{last_trades}",
                    "Mode apprentissage extrême → volume maximum prioritaire"
                ],
                "risks": [],
                "confidence": 0.98,
                "recommendation": "FORCE MAX TRADES — Apprentissage prioritaire (veto ignoré)",
                "full_summary": f"Je force le volume maximum. Actuellement : {positions_str} | {lessons_str}. Derniers trades visibles ci-dessus.",
                "final_decision": "BUY",
                "live_status": {
                    "open_positions": open_positions,
                    "lesson_count": lesson_count,
                    "recent_trades": recent_trades
                }
            }

        # VETO TRÈS FORT sur les worst patterns (mode précision)
        worst_patterns = context.get("worst_patterns", [])
        if worst_patterns:
            for p in worst_patterns:
                if p.get("win_rate", 1.0) <= 0.35 and p.get("occurrences", 0) >= 5:
                    return {
                        "agent": self.name,
                        "decision": "NO TRADE",
                        "summary": f"⛔ VETO — Pattern perdant détecté ({p.get('pattern')})",
                        "confidence": 0.98,
                        "recommendation": "Éviter ce symbole pour le moment",
                    }

        # (le reste du code original reste IDENTIQUE – aucune ligne supprimée)
        final_decision = "HOLD"
        reason         = "Pas de consensus clair"

        if "CRITICAL" in risk_summary or "STOP" in risk_reco:
            final_decision = "NO TRADE"
            reason = "Veto risque critique — capital à protéger"

        elif degraded:
            final_decision = "NO TRADE"
            reason = "Performance en dégradation détectée — pause prudente"

        elif streak_type == "loss" and streak_count >= 5:
            final_decision = "NO TRADE"
            reason = f"Série de {streak_count} pertes consécutives — pause obligatoire"

        elif has_buy and not has_sell:
            effective_score = (score + global_score) / 2
            if (effective_score >= 0.45 and
                    "CRITICAL" not in risk_reco and
                    "STOP" not in risk_reco):
                final_decision = "BUY"
                reason = (
                    f"Confluence haussière | score={effective_score:.2f} "
                    f"| mémoire={lesson_count} leçons"
                )
            else:
                final_decision = "HOLD"
                reason = f"Signal BUY insuffisant ou risque trop élevé"

        elif has_sell and not has_buy:
            if score <= 0.40:
                final_decision = "SELL"
                reason = "Confluence baissière confirmée"
            else:
                final_decision = "HOLD"
                reason = "Signal SELL faible — pas assez de confluence"

        if final_decision == "HOLD" and auto_rules and score >= 0.58:
            buy_rules = [r for r in auto_rules if "✅" in r]
            if len(buy_rules) >= 2:
                final_decision = "BUY"
                reason = f"Règles automatiques validées ({len(buy_rules)} règles actives)"

        insight_str = ""
        if insights:
            insight_str = " | Insight: " + insights[0][:60]

        confidence_final = round(max(0.50, min(0.95, (score + global_score) / 2 * 0.9)), 2)

        # === UPGRADE GROK-LIKE : RAISONNEMENT NATUREL PROFESSIONNEL ===
        natural_summary = (
            f"Salut, j’ai tout analysé avec l’équipe. "
            f"Après avoir croisé les leçons, les stats live, le risque et les signaux des autres agents sur {symbol}, "
            f"je recommande {final_decision.lower() if final_decision != 'HOLD' else 'de rester en attente'}. "
            f"{reason}. "
            f"On a déjà {lesson_count} leçons en mémoire, donc la décision est basée sur une vraie expérience passée."
        )

        return {
            "agent": self.name,
            "decision": final_decision,
            "summary": natural_summary,
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
            "full_summary": natural_summary,
            "final_decision": final_decision,
        }
