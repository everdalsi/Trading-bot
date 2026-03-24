class Memory:
    def __init__(self):
        self.trades = []
        self.wins = []
        self.losses = []
        self.mistakes = []

    def add_trade(self, trade):
        self.trades.append(trade)

        if trade["result"] == "win":
            self.wins.append(trade)
        else:
            self.losses.append(trade)

    def add_mistake(self, mistake):
        self.mistakes.append(mistake)

    def stats(self):
        total = len(self.trades)
        winrate = len(self.wins) / total if total > 0 else 0

        return {
            "total_trades": total,
            "winrate": winrate,
            "losses": len(self.losses)
        }
