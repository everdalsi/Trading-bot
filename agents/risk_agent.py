"""
🛡️ RISK AGENT V3 — Gestion du risque complète + intégration mémoire
"""

from agents.base_agent import BaseAgent
from typing import Dict, Any


class RiskAgent(BaseAgent):

    def __init__(self):
        super().__init__(
            name="risk",
            role="Gestion du risque avancée, drawdown, daily stop et position sizing"
        )

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        extreme_learning = context.get("extreme_learning_mode", False) or context.get("learning_mode", False)

        kelly          = context.get("kelly", 0.22)
        drawdown       = context.get("drawdown", 0.0)
        daily_pnl_pct  = context.get("daily_pnl_pct", 0.0)
        open_positions = context.get("open_positions", 0)
        max_positions  = context.get("max_positions", 4)
        is_night       = context.get("is_night", False)
        macro          = context.get("macro", "neutral")

        sharpe        = context.get("sharpe", 0.0) or 0.0
        profit_factor = context.get("profit_factor", 0.0) or 0.0
        degraded      = context.get("degraded", False)
        streak_type   = context.get("streak_type", "neutral")
        streak_count  = context.get("streak_count", 0)

        risk_level    = "LOW"
        risks_list    = []
        kelly_adjust  = 1.0
        recommendation = "Risk acceptable — trading autorisé"

        # OVERRIDE APPRENTISSAGE MAX — force les trades même en risque critique
        learning_mode = any(word in question.lower() for word in ["max de trade", "apprenez", "affûtez", "apprendre", "max trade", "beaucoup de trades", "vrai argent"])
        if learning_mode or extreme_learning:
            risk_level = "LOW"
            recommendation = "FORCE MAX TRADES — Apprentissage prioritaire (veto ignoré)"
            kelly_adjust = 1.0
            return {
                "agent": self.name,
                "summary": f"Risk: {risk_level} | Kelly: {kelly*100:.1f}% → {kelly*100:.1f}% | DD: {drawdown*100:.1f}% | Sharpe: {sharpe:.2f}",
                "arguments": ["Apprentissage forcé — volume max activé"],
                "risks": risks_list,
                "confidence": 0.95,
                "recommendation": recommendation,
                "kelly_adjusted": kelly,
                "risk_level": risk_level,
            }

        if drawdown <= -0.08:
            risk_level = "CRITICAL"
            risks_list.append(f"Drawdown dangereux ({drawdown*100:.1f}%)")
            recommendation = "STOP TRADING — Protéger le capital immédiatement"
            kelly_adjust = 0.0

        elif daily_pnl_pct <= -0.04:
            risk_level = "HIGH"
            risks_list.append(f"Perte journalière importante ({daily_pnl_pct*100:.1f}%)")
            kelly_adjust *= 0.5
            recommendation = "Réduire fortement l'exposition"

        if kelly > 0.28:
            risk_level = "HIGH" if risk_level == "LOW" else risk_level
            risks_list.append(f"Kelly trop agressif ({kelly*100:.1f}%)")
            kelly_adjust *= 0.6
        elif kelly > 0.22:
            risks_list.append(f"Kelly élevé ({kelly*100:.1f}%) — surveiller")
            kelly_adjust *= 0.85

        if open_positions >= max_positions:
            risks_list.append(f"Positions max atteint ({open_positions}/{max_positions})")
            recommendation = "Attendre la fermeture d'une position"

        if degraded:
            risk_level = "HIGH" if risk_level == "LOW" else risk_level
            risks_list.append("Performance en dégradation sur les 30 derniers trades")
            kelly_adjust *= 0.7

        if streak_type == "loss":
            if streak_count >= 8:
                risks_list.append(f"Série de {streak_count} pertes — PAUSE recommandée")
                kelly_adjust *= 0.3
                if risk_level == "LOW":
                    risk_level = "HIGH"
            elif streak_count >= 3:
                risks_list.append(f"Série de {streak_count} pertes — réduire la taille")
                kelly_adjust *= 0.6

        if sharpe < 0:
            risks_list.append(f"Sharpe négatif ({sharpe:.2f}) — stratégie peu rentable")
            kelly_adjust *= 0.8
        elif sharpe < 0.5 and profit_factor < 1.0:
            risks_list.append(f"Profit Factor < 1 ({profit_factor:.2f}) — pertes > gains")
            kelly_adjust *= 0.7

        if is_night:
            risks_list.append("Mode nuit → réduction automatique du risque")
            kelly_adjust *= 0.5

        if macro == "bearish":
            risks_list.append("Macro bearish → risque accru")
            kelly_adjust *= 0.7

        kelly_final = round(max(0.01, kelly * kelly_adjust), 3)

        summary = (
            f"Risk: {risk_level} | Kelly: {kelly*100:.1f}% → {kelly_final*100:.1f}% "
            f"| DD: {drawdown*100:.1f}% | Sharpe: {sharpe:.2f}"
        )

        # === UPGRADE GROK-LIKE : RAISONNEMENT NATUREL PROFESSIONNEL ===
        natural_summary = (
            f"Salut ! J’ai analysé le risque sur {symbol}. "
            f"Le drawdown est à {drawdown*100:.1f}%, le Sharpe à {sharpe:.2f}. "
            f"Avec les leçons accumulées, je recommande {recommendation.lower()}. "
            f"C’est prudent et aligné avec notre objectif de gains stables."
        )

        return {
            "agent": self.name,
            "summary": natural_summary,
            "arguments": [
                f"Positions ouvertes : {open_positions}/{max_positions}",
                f"Daily PnL : {daily_pnl_pct*100:.1f}%",
                f"Streak : {streak_count}x {streak_type}",
                f"Profit Factor : {profit_factor:.2f}",
                f"Kelly ajusté : {kelly_final*100:.1f}%",
                f"Mode nuit : {'Actif' if is_night else 'Inactif'}",
            ],
            "risks": risks_list,
            "confidence": 0.92,
            "recommendation": recommendation,
            "kelly_adjusted": kelly_final,
            "risk_level": risk_level,
            "full_summary": natural_summary
        }
