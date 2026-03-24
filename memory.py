class Memory:
    def __init__(self):
        self.data = {}

    def _init_symbol(self, symbol):
        if symbol not in self.data:
            self.data[symbol] = {
                "trades": [],
                "wins": 0,
                "losses": 0,
                "mistakes": [],
                "total_confidence": 0
            }

    # 🔥 ADD TRADE (SAFE)
    def add_trade(self, trade):
        symbol = trade["symbol"]
        self._init_symbol(symbol)

        self.data[symbol]["trades"].append(trade)

        # 🧠 accumulate confidence
        self.data[symbol]["total_confidence"] += trade.get("confidence", 0)

        # ✅ only count resolved trades
        if trade["result"] == "win":
            self.data[symbol]["wins"] += 1
        elif trade["result"] == "loss":
            self.data[symbol]["losses"] += 1
        # 👉 pending = ignored

    # 🔄 UPDATE RESULT (ULTRA IMPORTANT)
    def update_trade_result(self, symbol, index, result):
        self._init_symbol(symbol)

        trade = self.data[symbol]["trades"][index]
        trade["result"] = result

        if result == "win":
            self.data[symbol]["wins"] += 1
        elif result == "loss":
            self.data[symbol]["losses"] += 1

    # ❌ MISTAKES
    def add_mistake(self, symbol, mistake):
        self._init_symbol(symbol)
        self.data[symbol]["mistakes"].append(mistake)

    # 📊 STATS
    def stats(self, symbol):
        self._init_symbol(symbol)

        data = self.data[symbol]

        resolved = data["wins"] + data["losses"]
        winrate = data["wins"] / resolved if resolved > 0 else 0

        avg_confidence = (
            data["total_confidence"] / len(data["trades"])
            if len(data["trades"]) > 0 else 0
        )

        return {
            "symbol": symbol,
            "total_trades": len(data["trades"]),
            "resolved_trades": resolved,
            "winrate": winrate,
            "losses": data["losses"],
            "avg_confidence": avg_confidence
        }

    # 💀 BLACKLIST AUTO
    def is_bad_symbol(self, symbol):
        stats = self.stats(symbol)

        return (
            stats["resolved_trades"] > 10 and
            stats["winrate"] < 0.4
        )

    # 🔥 SCORE GLOBAL PAR COIN
    def get_symbol_score(self, symbol):
        stats = self.stats(symbol)

        score = (
            stats["winrate"] * 0.7 +
            stats["avg_confidence"] * 0.3
        )

        return round(score, 2)
