"""SMC (Smart Money Concepts) signal — informational only, NOT a gate.

From a technique described in a saved TikTok trading-education video
(Kasper Trading, "STRATEGIE 90k$/mois"): market structure (BOS/CHoCH),
liquidity zones (equal highs/lows), and order blocks. Independently
confirmed by a second creator (Adz Trades) describing the same "fake
CHoCH via unswept liquidity" pattern -- generic/mainstream SMC theory,
not proprietary alpha, but a real structured framework worth testing.

Same verify-before-trust discipline as social_signal.py: computed and
tagged onto every MICRO entry for later correlation analysis (does
`structure == "BULLISH" and bos` actually predict better outcomes than
the existing RSI/Bollinger/EMA signal alone?) -- NOT used to gate or
size trades yet. See the 2026-08-11 entry-signal-quality audit in
project memory for why this matters: BB+volume+RSI already dominate
nearly every MICRO entry, and this is a genuinely different signal
family worth checking for real differentiation before trusting it.

Uses only close-price data (bot.py's get_klines_1m_cached() doesn't
expose high/low) -- swing points are detected from a rolling window of
closes, a simplification of "real" SMC (usually done on wicks) chosen
to keep this additive and avoid touching the shared data_handler cache
that many other parts of the bot depend on.
"""

SWING_WINDOW = 3        # bars each side a point must beat to count as a swing high/low
LIQUIDITY_TOLERANCE = 0.002  # 0.2% -- "near a prior level" proxy for equal highs/lows
MIN_CLOSES_REQUIRED = 20


def _find_swings(closes):
    """Local extrema in a close-price series. Returns (swing_high_idx, swing_low_idx)."""
    highs, lows = [], []
    n = len(closes)
    for i in range(SWING_WINDOW, n - SWING_WINDOW):
        seg = closes[i - SWING_WINDOW: i + SWING_WINDOW + 1]
        if closes[i] == max(seg):
            highs.append(i)
        if closes[i] == min(seg):
            lows.append(i)
    return highs, lows


def analyze_structure(closes) -> dict:
    """Best-effort SMC-flavored features for a close-price series (oldest
    first, most recent last). Never raises -- returns a mostly-empty dict
    on any error or insufficient data."""
    result = {
        "structure": None,        # "BULLISH" | "BEARISH" | "RANGING" | None
        "bos": False,              # break of structure in the trend direction
        "choch": False,            # change of character (against the trend)
        "near_liquidity": False,   # price near a recent equal-high/low level
        "in_order_block": False,   # price retracing into the pre-impulse zone
    }
    try:
        closes = [float(c) for c in closes]
        if len(closes) < MIN_CLOSES_REQUIRED:
            return result

        highs_idx, lows_idx = _find_swings(closes)
        if len(highs_idx) < 2 or len(lows_idx) < 2:
            return result

        last_two_highs = [closes[i] for i in highs_idx[-2:]]
        last_two_lows = [closes[i] for i in lows_idx[-2:]]

        higher_highs = last_two_highs[-1] > last_two_highs[-2]
        higher_lows = last_two_lows[-1] > last_two_lows[-2]
        lower_highs = last_two_highs[-1] < last_two_highs[-2]
        lower_lows = last_two_lows[-1] < last_two_lows[-2]

        if higher_highs and higher_lows:
            result["structure"] = "BULLISH"
        elif lower_highs and lower_lows:
            result["structure"] = "BEARISH"
        else:
            result["structure"] = "RANGING"

        current_price = closes[-1]
        last_swing_high = last_two_highs[-1]
        last_swing_low = last_two_lows[-1]

        if result["structure"] == "BULLISH" and current_price > last_swing_high:
            result["bos"] = True
        elif result["structure"] == "BEARISH" and current_price < last_swing_low:
            result["bos"] = True

        if result["structure"] == "BULLISH" and current_price < last_swing_low:
            result["choch"] = True
        elif result["structure"] == "BEARISH" and current_price > last_swing_high:
            result["choch"] = True

        for lvl in last_two_highs + last_two_lows:
            if lvl and abs(current_price - lvl) / lvl <= LIQUIDITY_TOLERANCE:
                result["near_liquidity"] = True
                break

        # Order-block proxy: the candle immediately before the most recent
        # swing pivot -- the "last thing smart money did before the move".
        if lows_idx and highs_idx:
            pivot_i = max(lows_idx[-1], highs_idx[-1])
            if pivot_i > 0:
                ob_zone_price = closes[pivot_i - 1]
                if ob_zone_price and abs(current_price - ob_zone_price) / ob_zone_price <= LIQUIDITY_TOLERANCE:
                    result["in_order_block"] = True

        return result
    except Exception:
        return result
