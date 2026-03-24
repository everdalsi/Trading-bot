from typing import Dict, Any, List

class Memory:
    def __init__(self):
        self.data: Dict[str, dict] = {}

    def _init_symbol(self, symbol: str):
        if symbol not in self.data:
            self.data[symbol] = {
                "trades": [],
                "wins": 0,
                "losses": 0,
                "mistakes": [],
                "total_confidence": 0.0
            }

    # 🔥 ADD TRADE (SAFE)
    def add_trade(self, trade: dict):
        symbol = trade.get("symbol")
        if not symbol:
            return
        self._init_symbol(symbol)

        self.data[symbol]["trades"].append(trade)

        # Accumule la confiance
        self.data[symbol]["total_confidence"] += trade.get("confidence", 0.0)

        # Ne compte que les trades résolus
        result = trade.get("result")
        if result == "win":
            self.data[symbol]["wins"] += 1
        elif result == "loss":
            self.data[symbol]["losses"] += 1

    # 🔄 UPDATE RESULT
    def update_trade_result(self, symbol: str, index: int, result: str):
        self._init_symbol(symbol)
        try:
            trade = self.data[symbol]["trades"][index]
            trade["result"] = result

            if result == "win":
                self.data[symbol]["wins"] += 1
            elif result == "loss":
                self.data[symbol]["losses"] += 1
        except (IndexError, KeyError):
            pass

    # ❌ MISTAKES
    def add_mistake(self, symbol: str, mistake: str):
        self._init_symbol(symbol)
        self.data[symbol]["mistakes"].append(mistake)

    # 📊 STATS par symbole
    def stats(self, symbol: str) -> dict:
        self._init_symbol(symbol)
        data = self.data[symbol]

        resolved = data["wins"] + data["losses"]
        winrate = data["wins"] / resolved if resolved > 0 else 0.0

        avg_confidence = (
            data["total_confidence"] / len(data["trades"])
            if len(data["trades"]) > 0 else 0.0
        )

        return {
            "symbol": symbol,
            "total_trades": len(data["trades"]),
            "resolved_trades": resolved,
            "winrate": round(winrate, 4),
            "losses": data["losses"],
            "avg_confidence": round(avg_confidence, 4)
        }

    # 💀 BLACKLIST AUTO
    def is_bad_symbol(self, symbol: str) -> bool:
        stats = self.stats(symbol)
        return stats["resolved_trades"] > 10 and stats["winrate"] < 0.40

    # 🔥 SCORE GLOBAL PAR COIN
    def get_symbol_score(self, symbol: str) -> float:
        stats = self.stats(symbol)
        score = stats["winrate"] * 0.7 + stats["avg_confidence"] * 0.3
        return round(score, 2)

    # === MÉTHODES AJOUTÉES POUR COMPATIBILITÉ ORCHESTRATOR ===
    def get_global_stats(self) -> dict:
        total_wins = sum(d["wins"] for d in self.data.values())
        total_losses = sum(d["losses"] for d in self.data.values())
        total = total_wins + total_losses
        winrate = total_wins / total if total > 0 else 0.0
        return {
            "total_trades": total,
            "wins": total_wins,
            "losses": total_losses,
            "winrate": round(winrate, 2)
        }

    def update_trade_results(self, current_price: float):
        """Met à jour tous les trades pending avec le prix actuel."""
        for symbol in list(self.data.keys()):
            for i, trade in enumerate(self.data[symbol]["trades"]):
                if trade.get("result") == "pending":
                    # Calcul simplifié PNL (comme dans bot.py)
                    entry = trade.get("price_in") or trade.get("entry_price")
                    if entry:
                        pnl_pct = (current_price - entry) / entry if trade.get("decision") == "BUY" else (entry - current_price) / entry
                        trade["pnl_pct"] = round(pnl_pct * 100, 2)
                        trade["result"] = "win" if pnl_pct > 0 else "loss"
                        self.update_trade_result(symbol, i, trade["result"])

    def log_trade(self, trade_data: dict):
        """Ajoute un trade pending (utilisé par l'orchestrator)."""
        self.add_trade(trade_data)
