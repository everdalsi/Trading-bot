def compute_strategy_score(memory):
    trades = memory.get("trades", [])

    if not trades:
        return 0.5  # neutre

    wins = sum(1 for t in trades if t.get("result") == "win")
    losses = sum(1 for t in trades if t.get("result") == "loss")

    total = wins + losses
    if total == 0:
        return 0.5

    winrate = wins / total

    return winrate


def adjust_confidence(base_confidence, memory):
    score = compute_strategy_score(memory)

    # 🔥 adaptation intelligente
    if score > 0.6:
        boost = 0.1
    elif score < 0.4:
        boost = -0.15
    else:
        boost = 0

    new_confidence = base_confidence + boost

    # clamp entre 0.1 et 0.95
    return max(0.1, min(0.95, new_confidence))
