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

`analyze_structure()` uses only close-price data -- swing points are
detected from a rolling window of closes, a simplification of "real" SMC
(usually done on wicks). This was originally a deliberate limitation
(bot.py's get_klines_1m_cached() didn't expose high/low at the time).

UPDATE (2026-09-24): data_handler.py now also caches highs/lows
(get_ohlc_1m_cached(), additive, zero extra API calls -- see its
docstring). Two more indicators added below that genuinely NEED wicks
and couldn't be approximated on closes alone, found in another saved
TikTok trading-education video (@captaintrading) and verified against
real technical-analysis sources before implementing (not taken at
face value -- see sources in each docstring):

- Choppiness Index (CHOP) -- E.W. Dreiss, standard formula, needs
  high/low for its ATR component.
- Swing Failure Pattern (SFP) -- an ICT/SMC concept literally defined
  by wick behavior (price wicks past a prior swing level then closes
  back inside it same bar) -- cannot be detected on close-only data at
  all, this is the reason it wasn't implemented before now.

Same discipline as everything else in this file: computed and tagged
onto the trade for later correlation analysis, NOT used to gate or
size trades yet, never raises.
"""

SWING_WINDOW = 3        # bars each side a point must beat to count as a swing high/low
LIQUIDITY_TOLERANCE = 0.002  # 0.2% -- "near a prior level" proxy for equal highs/lows
MIN_CLOSES_REQUIRED = 20
CHOP_PERIOD = 14                # standard Dreiss period
CHOP_CHOPPY_THRESHOLD = 61.8    # above this: ranging/choppy market
CHOP_TRENDING_THRESHOLD = 38.2  # below this: strong trend
SFP_LOOKBACK = 20               # bars searched for the prior swing level being swept

import math


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


def compute_chop(highs, lows, closes, period: int = CHOP_PERIOD) -> float | None:
    """Choppiness Index (E.W. Dreiss): 100*LOG10(SUM(TrueRange,n)/(MaxHigh(n)-MinLow(n)))/LOG10(n).
    >CHOP_CHOPPY_THRESHOLD (61.8) = ranging/choppy. <CHOP_TRENDING_THRESHOLD (38.2) = strong trend.
    Formula verified against https://www.wealthcharts.com/kb (Choppiness Index formula) and
    https://www.tradingsim.com/blog/choppiness-index-indicator before implementing.
    None on insufficient data or any error -- never raises."""
    try:
        highs = [float(h) for h in highs]
        lows = [float(l) for l in lows]
        closes = [float(c) for c in closes]
        n = period
        if len(highs) < n + 1 or len(lows) < n + 1 or len(closes) < n + 1:
            return None

        trs = []
        start = len(highs) - n
        for i in range(start, len(highs)):
            if i == 0:
                tr = highs[i] - lows[i]
            else:
                tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        atr_sum = sum(trs)
        rng = max(highs[-n:]) - min(lows[-n:])
        if rng <= 0 or atr_sum <= 0:
            return None
        return 100 * math.log10(atr_sum / rng) / math.log10(n)
    except Exception:
        return None


def chop_regime(chop_value: float | None) -> str:
    """UNKNOWN | CHOPPY | TRENDING | NEUTRAL, from a compute_chop() output."""
    if chop_value is None:
        return "UNKNOWN"
    if chop_value >= CHOP_CHOPPY_THRESHOLD:
        return "CHOPPY"
    if chop_value <= CHOP_TRENDING_THRESHOLD:
        return "TRENDING"
    return "NEUTRAL"


def detect_sfp(highs, lows, closes, lookback: int = SFP_LOOKBACK) -> dict:
    """Swing Failure Pattern: the last bar's wick sweeps a prior swing high/low
    (from the `lookback` bars before it) but closes back inside -- a liquidity-sweep
    reversal signal. Real ICT/SMC concept, verified against
    https://www.luxalgo.com/library/indicator/swing-failure-pattern-sfp/ and
    https://coinmarketcap.com/academy/article/what-is-the-swing-failure-pattern-and-how-to-use-it-in-trading
    before implementing. Never raises -- returns all-False on insufficient data/error."""
    result = {"bullish_sfp": False, "bearish_sfp": False, "swept_level": None}
    try:
        highs = [float(h) for h in highs]
        lows = [float(l) for l in lows]
        closes = [float(c) for c in closes]
        if len(highs) < lookback + 2 or len(lows) < lookback + 2 or len(closes) < lookback + 2:
            return result

        prior_highs = highs[-(lookback + 1):-1]
        prior_lows = lows[-(lookback + 1):-1]
        prior_swing_high = max(prior_highs)
        prior_swing_low = min(prior_lows)
        last_high, last_low, last_close = highs[-1], lows[-1], closes[-1]

        if last_high > prior_swing_high and last_close < prior_swing_high:
            result["bearish_sfp"] = True
            result["swept_level"] = prior_swing_high
        if last_low < prior_swing_low and last_close > prior_swing_low:
            result["bullish_sfp"] = True
            result["swept_level"] = prior_swing_low
        return result
    except Exception:
        return result


def analyze_ohlc(highs, lows, closes) -> dict:
    """Combined CHOP + SFP read for one symbol -- the single call site to wire into
    bot.py's MICRO signal, same shape/spirit as analyze_structure(). Never raises."""
    chop = compute_chop(highs, lows, closes)
    sfp = detect_sfp(highs, lows, closes)
    return {
        "chop": chop,
        "chop_regime": chop_regime(chop),
        "sfp_bullish": sfp["bullish_sfp"],
        "sfp_bearish": sfp["bearish_sfp"],
        "sfp_level": sfp["swept_level"],
    }
