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

        # === COLLECTIVE BRAIN : SYNTHÈSE DE LA DISCUSSION ENTRE AGENTS ===
        # On regarde les réponses raffinées des agents après leur deuxième round
        collective_consensus = "HOLD"
        collective_reason = "Discussion collective en cours"

        # Compter les votes BUY / SELL / NO TRADE des agents
        buy_votes = 0
        sell_votes = 0
        no_trade_votes = 0
        total_confidence = 0

        for resp in agent_outputs:
            if isinstance(resp, dict):
                dec = str(resp.get("decision", "HOLD")).upper()
                conf = resp.get("confidence", 0.5)
                total_confidence += conf
                if dec == "BUY":
                    buy_votes += 1
                elif dec == "SELL":
                    sell_votes += 1
                elif dec == "NO TRADE":
                    no_trade_votes += 1

        avg_confidence = total_confidence / max(len(agent_outputs), 1)

        if buy_votes > sell_votes + no_trade_votes and avg_confidence > 0.65:
            collective_consensus = "BUY"
            collective_reason = f"Consensus haussier fort ({buy_votes} agents sur {len(agent_outputs)})"
        elif sell_votes > buy_votes + no_trade_votes and avg_confidence > 0.65:
            collective_consensus = "SELL"
            collective_reason = f"Consensus baissier fort ({sell_votes} agents sur {len(agent_outputs)})"
        elif no_trade_votes > buy_votes + sell_votes or avg_confidence < 0.55:
            collective_consensus = "NO TRADE"
            collective_reason = "Consensus prudent — trop d’incertitudes ou risques élevés"

        # === UPGRADE GROK-LIKE : RAISONNEMENT NATUREL PROFESSIONNEL ===
        natural_summary = (
            f"Salut, j’ai écouté toute l’équipe. "
            f"Après que chaque agent ait analysé sa partie et qu’ils aient discuté ensemble, "
            f"le consensus sur {symbol} est de {collective_consensus.lower() if collective_consensus != 'NO TRADE' else 'ne pas prendre de position pour l’instant'}. "
            f"{collective_reason}. "
            f"On a déjà {lesson_count} leçons en mémoire, donc la décision est solide et basée sur une vraie expérience collective."
        )

        return {
            "agent": self.name,
            "decision": collective_consensus,
            "summary": natural_summary,
            "arguments": [
                f"Symbol: {symbol}",
                f"Score composite: {(score + global_score) / 2:.2f}",
                f"Trader: {trader_decision_val} | Risk: {risk_summary[:50]}",
                f"Leçons en mémoire: {lesson_count}",
                f"Règles auto actives: {len(auto_rules)}",
            ],
            "risks": (
                [] if collective_consensus in ("BUY", "SELL") else
                ["Décision bloquée — voir raison ci-dessus"]
            ),
            "confidence": round(avg_confidence, 2),
            "recommendation": collective_reason,
            "full_summary": natural_summary,
            "final_decision": collective_consensus,
        }
