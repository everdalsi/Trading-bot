# ==============================
# 🧠 LEARNING AGENT V2
# ==============================

def compute_global_score(memory):
    trades = memory.get("trades", [])

    if not trades:
        return 0.5

    wins = sum(1 for t in trades if t.get("result") == "win")
    losses = sum(1 for t in trades if t.get("result") == "loss")

    total = wins + losses
    if total == 0:
        return 0.5

    return wins / total


def compute_symbol_score(memory, symbol):
    trades = memory.get("trades", [])

    # filtre par crypto
    symbol_trades = [t for t in trades if t.get("symbol") == symbol]

    if not symbol_trades:
        return 0.5

    wins = sum(1 for t in symbol_trades if t.get("result") == "win")
    losses = sum(1 for t in symbol_trades if t.get("result") == "loss")

    total = wins + losses
    if total == 0:
        return 0.5

    return wins / total


def adjust_confidence(base_confidence, memory, symbol=None):
    # 🔥 priorité au score par coin
    if symbol:
        score = compute_symbol_score(memory, symbol)
    else:
        score = compute_global_score(memory)

    # 🔥 adaptation intelligente
    if score > 0.65:
        boost = 0.15
    elif score < 0.4:
        boost = -0.2
    else:
        boost = 0

    new_confidence = base_confidence + boost

    # clamp sécurité
    return max(0.1, min(0.95, new_confidence))


def should_blacklist(memory, symbol):
    score = compute_symbol_score(memory, symbol)

    # 🔥 blacklist si trop mauvais
    return score < 0.3


def get_learning_summary(memory):
    trades = memory.get("trades", [])

    wins = sum(1 for t in trades if t.get("result") == "win")
    losses = sum(1 for t in trades if t.get("result") == "loss")

    total = wins + losses
    winrate = wins / total if total > 0 else 0.5

    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "winrate": round(winrate, 2)
    }
