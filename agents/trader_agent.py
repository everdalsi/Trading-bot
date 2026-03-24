from agents.base_agent import BaseAgent
from agents.learning_agent import adjust_confidence, compute_symbol_score, should_blacklist


class TraderAgent(BaseAgent):
    def __init__(self):
        super().__init__("trader", "Décision trading")

    async def respond(self, question, context):
        macro = context.get("macro", "neutral")
        memory = context.get("memory", {})
        symbol = context.get("symbol", "UNKNOWN")
        price = context.get("price")

        # 🔥 0. BLACKLIST CHECK
        if should_blacklist(memory, symbol):
            return {
                "agent": self.name,
                "symbol": symbol,
                "decision": "SKIP",
                "confidence": 0,
                "summary": f"{symbol} blacklisté ❌",
                "reason": "mauvaises performances historiques"
            }

        # 🔥 1. BASE DECISION (direction)
        if macro == "bullish":
            base_decision = "BUY"
            base_confidence = 0.7
        elif macro == "bearish":
            base_decision = "SELL"
            base_confidence = 0.7
        else:
            base_decision = "HOLD"
            base_confidence = 0.5

        # 🧠 2. LEARNING PAR SYMBOL
        confidence = adjust_confidence(base_confidence, memory, symbol)

        # 🔥 3. DECISION FINALE
        if confidence < 0.4:
            decision = "HOLD"  # trop risqué
        else:
            decision = base_decision

        # 🚫 4. ANTI OVERTRADING (évite spam trades)
        recent_trades = memory.get("trades", [])[-5:]
        same_symbol_trades = [t for t in recent_trades if t.get("symbol") == symbol]

        if len(same_symbol_trades) >= 2:
            decision = "HOLD"

        # 🧠 5. SAVE TRADE
        if "trades" not in memory:
            memory["trades"] = []

        trade = {
            "symbol": symbol,
            "decision": decision,
            "macro": macro,
            "confidence": confidence,
            "entry_price": price,
            "result": None
        }

        memory["trades"].append(trade)

        # 📊 6. SCORE
        score = compute_symbol_score(memory, symbol)

        return {
            "agent": self.name,
            "symbol": symbol,
            "decision": decision,
            "confidence": round(confidence, 2),
            "symbol_score": round(score, 2),
            "summary": f"{symbol} | {decision} | score={round(score,2)}"
        }
