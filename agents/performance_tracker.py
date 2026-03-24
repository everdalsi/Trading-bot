def update_trade_results(memory, current_price):
    trades = memory.get("trades", [])

    for trade in trades:
        # ⚠️ skip si déjà traité
        if trade["result"] is not None:
            continue

        entry = trade.get("entry_price")
        decision = trade.get("decision")

        if entry is None:
            continue

        # 🔥 LOGIQUE SIMPLE
        if decision == "BUY":
            if current_price > entry:
                trade["result"] = "win"
            else:
                trade["result"] = "loss"

        elif decision == "SELL":
            if current_price < entry:
                trade["result"] = "win"
            else:
                trade["result"] = "loss"

        else:
            trade["result"] = "neutral"

    return memory
