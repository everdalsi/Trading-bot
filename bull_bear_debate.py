"""Bull/bear debate signal -- informational only, NOT a gate.

Pattern lifted from TradingAgents (TauricResearch/TradingAgents, 80k+ GitHub
stars -- verified as a real, well-established open-source multi-agent
trading architecture, found via a TikTok video review 2026-09-02), whose
core differentiator versus this bot's existing single-score `micro_signal()`
is a dedicated adversarial step: separate bull and bear cases are argued
*before* a decision, specifically to surface disagreement between signal
families that a single additive score can hide.

`micro_signal()`'s score already blends RSI/EMA/momentum/Bollinger into one
number -- if RSI says oversold-bounce and momentum says falling, they just
partially cancel in the sum with no visibility into the conflict. This
module doesn't re-score those (that's already `score`/`contributors`); it
specifically checks whether the *SMC structure signal* (a genuinely
different signal family, already computed and unused for gating -- see
smc_signal.py) agrees or conflicts with the direction the score implies.
That conflict/agreement is the debate's actual output.

Same discipline as smc_signal.py and social_signal.py before it: computed
and tagged onto every MICRO entry for later correlation analysis (does a
"bear case wins" flag on a BUY actually predict worse outcomes?), NOT used
to gate or size trades yet. Rule-based, not an LLM call, to avoid adding
latency/cost to the hot path -- consistent with why smc_signal.py doesn't
call Claude either.
"""


def debate(score: float, direction: str, smc_info: dict) -> dict:
    """Argue the bull case and the bear case for a candidate trade using
    signal families outside the base score (currently just SMC structure --
    room to add whale/order-book agreement later without changing the
    return shape). Never raises -- returns a neutral/empty result on error.

    `direction` is "BUY" or "SELL" (the side `micro_signal()` is about to
    return); `smc_info` is the dict from `smc_signal.analyze_structure()`.
    """
    result = {
        "bull_case": [],
        "bear_case": [],
        "smc_agrees": None,     # True/False/None (None = SMC had no structure read)
        "verdict": "no_data",   # "bull_wins" | "bear_wins" | "split" | "no_data"
    }
    try:
        structure = smc_info.get("structure") if smc_info else None
        if structure is None:
            return result

        wants_up = direction == "BUY"

        # The base score already argued its own case (contributors) --
        # the debate's job is to check whether SMC structure, an
        # independent signal family, corroborates or fights that case.
        if structure == "BULLISH":
            result["bull_case"].append("SMC structure: higher highs/higher lows")
            if smc_info.get("bos"):
                result["bull_case"].append("SMC: break of structure confirms uptrend")
            if smc_info.get("choch"):
                result["bear_case"].append("SMC: change of character -- uptrend losing control")
        elif structure == "BEARISH":
            result["bear_case"].append("SMC structure: lower highs/lower lows")
            if smc_info.get("bos"):
                result["bear_case"].append("SMC: break of structure confirms downtrend")
            if smc_info.get("choch"):
                result["bull_case"].append("SMC: change of character -- downtrend losing control")
        else:  # RANGING
            result["bear_case"].append("SMC: no clear structure (ranging) -- weak conviction either way")
            result["bull_case"].append("SMC: no clear structure (ranging) -- weak conviction either way")

        if smc_info.get("near_liquidity"):
            # A move into a liquidity zone cuts both ways: could be the
            # bounce/rejection point (supports current direction) or the
            # sweep-then-reverse trap (argues against it) -- flag both,
            # this is exactly the kind of ambiguity the debate should
            # surface rather than silently resolve.
            result["bull_case"].append("near a liquidity level (possible reaction zone)")
            result["bear_case"].append("near a liquidity level (possible stop-hunt/sweep risk)")

        if smc_info.get("in_order_block"):
            side = "bull_case" if wants_up else "bear_case"
            result[side].append("price retraced into a pre-impulse order-block zone")

        smc_direction_up = structure == "BULLISH"
        smc_direction_down = structure == "BEARISH"
        if structure == "RANGING":
            result["smc_agrees"] = None
        else:
            result["smc_agrees"] = (wants_up and smc_direction_up) or (not wants_up and smc_direction_down)

        if len(result["bull_case"]) > len(result["bear_case"]):
            result["verdict"] = "bull_wins"
        elif len(result["bear_case"]) > len(result["bull_case"]):
            result["verdict"] = "bear_wins"
        else:
            result["verdict"] = "split"

        return result
    except Exception:
        return result
