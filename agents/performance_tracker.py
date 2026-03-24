class PerformanceTracker:
    def __init__(self):
        pass

    def update_trade_results(self, memory, current_price):
        try:
            for symbol, data in memory.data.items():
                trades = data.get("trades", [])

                for trade in trades:
                    if trade.get("result") != "pending":
                        continue

                    entry = trade.get("entry_price")
                    decision = trade.get("decision")

                    if entry is None:
                        continue

                    if decision == "BUY":
                        if current_price > entry:
                            trade["result"] = "win"
                            data["wins"] += 1
                        else:
                            trade["result"] = "loss"
                            data["losses"] += 1

                    elif decision == "SELL":
                        if current_price < entry:
                            trade["result"] = "win"
                            data["wins"] += 1
                        else:
                            trade["result"] = "loss"
                            data["losses"] += 1

                    else:
                        trade["result"] = "neutral"

            return memory

        except Exception:
            return memory

    def get_global_stats(self, memory):
        try:
            total_wins = 0
            total_losses = 0

            for data in memory.data.values():
                total_wins += data.get("wins", 0)
                total_losses += data.get("losses", 0)

            total = total_wins + total_losses
            winrate = total_wins / total if total > 0 else 0

            return {
                "total_trades": total,
                "wins": total_wins,
                "losses": total_losses,
                "winrate": round(winrate, 2)
            }

        except Exception:
            return {}

    def get_symbol_stats(self, memory, symbol):
        try:
            data = memory.data.get(symbol, {})

            wins = data.get("wins", 0)
            losses = data.get("losses", 0)

            total = wins + losses
            winrate = wins / total if total > 0 else 0

            return {
                "symbol": symbol,
                "wins": wins,
                "losses": losses,
                "winrate": round(winrate, 2)
            }

        except Exception:
            return {}
