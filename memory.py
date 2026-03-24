class Memory:
    def __init__(self):
        self.data = {}  # 🔥 clé = symbol

    def _init_symbol(self, symbol):
        if symbol not in self.data:
            self.data[symbol] = {
                "trades": [],
                "wins": 0,
                "losses": 0,
                "mistakes": []
            }

    def add_trade(self, trade):
        symbol = trade["symbol"]
        self._init_symbol(symbol)

        self.data[symbol]["trades"].append(trade)

        if trade["result"] == "win":
            self.data[symbol]["wins"] += 1
        else:
            self.data[symbol]["losses"] += 1

    def add_mistake(self, symbol, mistake):
        self._init_symbol(symbol)
        self.data[symbol]["mistakes"].append(mistake)

    def stats(self, symbol):
        self._init_symbol(symbol)

        data = self.data[symbol]
        total = len(data["trades"])
        winrate = data["wins"] / total if total > 0 else 0

        return {
            "symbol": symbol,
            "total_trades": total,
            "winrate": winrate,
            "losses": data["losses"]
        }
