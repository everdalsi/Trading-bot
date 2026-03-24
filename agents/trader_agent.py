from agents.base_agent import BaseAgent
from agents.learning_agent import LearningAgent
from typing import Dict, Any

class TraderAgent(BaseAgent):
    """
    TraderAgent V3 - Décision finale d'achat/vente
    Combine macro, score learning, analyse et risk pour une décision intelligente.
    """

    def __init__(self):
        super().__init__("trader", "Décision trading (BUY / SELL / HOLD)")

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        macro = context.get("macro", "neutral")
        symbol = context.get("symbol", "UNKNOWN")
        price = context.get("price")
        analysis = context.get("analysis", {})
        risk = context.get("risk", {})
        learning = context.get("learning", {})
        global_score = context.get("score", 0.5)

        # Instance du LearningAgent
        learning_agent = LearningAgent()

        # 1. BLACKLIST via LearningAgent
        blacklist_check = await learning_agent.respond("should I blacklist this symbol?", context)
        if blacklist_check.get("recommendation", "").lower().startswith("éviter"):
            return {
                "agent": self.name,
                "symbol": symbol,
                "decision": "HOLD",
                "confidence": 0.0,
                "summary": f"{symbol} blacklisté par Learning Agent",
                "reason": "mauvaises performances historiques"
            }

        # 2. Score par symbole
        symbol_score = learning.get("symbol_score", global_score)

        # 3. Décision intelligente
        decision = "HOLD"
        reason = "Pas de signal clair"

        if macro == "bullish" and symbol_score >= 0.62 and global_score >= 0.65:
            if "CRITICAL" not in str(risk.get("summary", "")) and "HIGH RISK" not in str(risk.get("recommendation", "")):
                decision = "BUY"
                reason = "Macro haussier + bon score learning + risque OK"
        elif macro == "bearish" and symbol_score <= 0.38 and global_score <= 0.35:
            decision = "SELL"
            reason = "Macro baissier + faible score learning"
        elif symbol_score > 0.75:
            decision = "BUY"
            reason = "Score symbole très fort"
        elif symbol_score < 0.25:
            decision = "HOLD"
            reason = "Score symbole trop faible"

        # 4. Anti-overtrading
        recent_trades = context.get("memory", {}).get("trades", [])[-8:]
        same_symbol = [t for t in recent_trades if t.get("symbol") == symbol]
        if len(same_symbol) >= 3:
            decision = "HOLD"
            reason = "Trop de trades récents sur ce symbole"

        # 5. Confiance finale
        confidence = round(max(0.1, min(0.95, global_score * 1.1 if decision != "HOLD" else 0.4)), 2)

        return {
            "agent": self.name,
            "symbol": symbol,
            "decision": decision,
            "confidence": confidence,
            "symbol_score": round(symbol_score, 2),
            "summary": f"{symbol} → {decision} | score={round(symbol_score, 2)} | conf={confidence}",
            "reason": reason,
            "macro": macro
        }
