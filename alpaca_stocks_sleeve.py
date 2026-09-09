"""
ALPACA US-STOCKS PAPER SLEEVE (2026-09-08).

New, additive sleeve alongside the existing crypto sleeves (MICRO on Binance
testnet, HL_COPY mirroring Hyperliquid wallets). Trades a small basket of US
large caps on Alpaca **paper** trading only -- alpaca_broker.py already
refuses to start against anything but a paper-api URL, and this module never
overrides that.

Design choices, explicit and small on purpose (per user instruction: a
simple, documented starting logic, not an invented complex strategy):

- Universe: AAPL, MSFT, NVDA, SPY. Four liquid large caps + the S&P 500 ETF
  as a market-beta reference point. Not meant to cover the whole US market --
  a deliberately small start, easy to extend once this sleeve is validated.
- Existing crypto technical agents (agents/regime_detector_agent.py,
  agents/vol_regime_agent.py, agents/pattern_recognition_agent.py) were
  checked before writing this: their *math* (ADX, choppiness, HV, pattern
  detection) is generic, but each one hardcodes a direct Binance REST fetch
  (`requests.get(f"{BINANCE_BASE}/api/v3/klines", ...)`) with no parameter to
  swap the data source. Reusing them would mean either duplicating them with
  an Alpaca fetch (defeats the point of reuse) or editing the shared agent
  files (risks the crypto sleeves that depend on them -- explicitly
  forbidden by the no-regression rule). So this sleeve uses its own small,
  self-contained scoring function instead, built the same way those agents
  are (trend + momentum on OHLC closes), just without the Binance coupling.
- Scoring (score_symbol below): SMA10 vs SMA30 crossover (trend) + 5-day
  return (momentum), each contributing -1/0/+1. Combined score in [-2, 2].
  score >= 2 -> BUY, score <= -2 -> SELL (only closes an existing long),
  else HOLD. Long-only: this paper account trades cash-covered positions,
  no shorting.
- State: own ledger file (alpaca_stocks_ledger.json), completely separate
  from sim_portfolio_v7.json (crypto). Alpaca's own $100k paper cash is the
  capital for this sleeve -- never combined with the crypto `sim` totals.
- Isolation: every symbol's fetch/score/order is wrapped in its own
  try/except so one bad symbol (bad data, rate limit, rejected order) can
  never take down the cycle for the others, and the whole cycle runs off
  the main thread (same threading.Thread(daemon=True) + running-flag guard
  pattern as run_hl_copytrade_cycle in bot.py) so a slow Alpaca API call can
  never block the crypto trading loop.

CONVICTION OVERLAY (added 2026-09-09, additive only -- see verification below):
  sec_13f_signal.py (institutional 13F filers) and congress_trades_signal.py
  (House STOCK Act disclosures) exist in this repo, isolated and tested, but
  were not wired into anything. Before integrating them here, both were
  actually queried live (not assumed) for this exact universe (AAPL, MSFT,
  NVDA, SPY):
    - 13F: every one of the 7 CURATED_FILERS has a real, current position
      change on at least one of these 4 tickers in their latest-vs-prior
      13F-HR (e.g. Berkshire +14% AAPL, Renaissance +222% NVDA, Third Point
      exited NVDA entirely, several funds opened new SPY positions).
    - Congress (House only -- Senate has no free source, see
      congress_trades_signal.py docstring): all 4 tickers have real,
      multi-filer trade disclosures within the last 90 days.
  So this is a real, additive signal for this universe, not a no-op.

  Weighting rule (deliberately small and capped -- these are 45-135 day-lag
  13F and multi-week-lag Congress disclosures, medium-term conviction hints,
  never real-time triggers):
    - 13f_direction (-1/0/+1): across the 7 curated filers, count filers
      whose latest-vs-prior 13F action on this ticker's CUSIP is
      NEW/INCREASED (bullish) minus EXIT/DECREASED (bearish). +1 if that net
      count is >=2, -1 if <=-2, else 0. Requires SEC filers matching on the
      exact CUSIP (see TARGET_CUSIPS), never a name substring (an early
      draft of this matched "PINEAPPLE INC" as "APPLE" -- fixed by using
      CUSIP only).
    - congress_direction (-1/0/+1): from conviction() over a 90-day
      disclosure-date lookback (30d missed real matches found during
      research; 90d balances freshness against Congress's slow, uneven
      filing lag), +1 if net_buy_pressure > 0 AND unique_filers >= 2 (single
      filer is too noisy), -1 if net_buy_pressure < 0 AND unique_filers >= 2,
      else 0.
    - conviction_bonus = clamp(13f_direction + congress_direction, -1, +1).
      A single -1/0/+1 modifier ADDED to the existing technical score
      (trend_score + momentum_score, range [-2,2]), giving a combined range
      of [-3,3] fed into the *same* decide() thresholds (>=2 BUY, <=-2 SELL).
      Because |conviction_bonus| <= 1 and the trigger threshold is 2, this
      overlay can never fire a trade on its own (bonus alone maxes at 1) --
      it can only tip an already-borderline technical score (+-1) over the
      threshold, or partially dampen a strong one. This is the "bonus/malus,
      not an autonomous signal" behavior required for this change.
    - If both directions are absent (0/0) for a ticker -- expected to be
      common as the underlying filings age between quarters/disclosures --
      conviction_bonus is 0 and the sleeve trades on technical score alone,
      unchanged from before this overlay existed.

  Fail-open, strictly: sec_13f_signal / congress_trades_signal are imported
  in a top-level try/except (missing module -> overlay silently disabled).
  Every network call they make is *additionally* wrapped per-source in
  compute_conviction_bonus() below, so a live failure (SEC down, GitHub
  raw file unreachable, rate limited, malformed data) can only zero out
  that one source's direction for that one cycle -- it can never raise out
  of compute_conviction_bonus(), never touches the technical score path,
  and never stops the other source or the other symbols. Results are
  disk-cached (own file, CONVICTION_CACHE_PATH) for CONVICTION_CACHE_MAX_AGE
  because both sources move on a multi-day/week cadence -- refetching 13F
  (5 SEC requests per filer x 7 filers) or the ~11MB House dataset every
  5-minute cycle would be wasteful and needlessly hammer SEC's rate limit.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import alpaca_broker

# Optional conviction overlay (13F + Congress) -- see module docstring
# "CONVICTION OVERLAY" section. Import failures must never break this
# sleeve: fall back to technical-only scoring.
try:
    import sec_13f_signal
    _SEC_13F_IMPORT_OK = True
except Exception:
    sec_13f_signal = None
    _SEC_13F_IMPORT_OK = False

try:
    import congress_trades_signal
    _CONGRESS_IMPORT_OK = True
except Exception:
    congress_trades_signal = None
    _CONGRESS_IMPORT_OK = False

UNIVERSE = ["AAPL", "MSFT", "NVDA", "SPY"]

# Verified live against SEC EDGAR 2026-09-09 (each cusip confirmed present,
# under this exact name, in real recent 13F-HR filings from CURATED_FILERS).
# Matching by CUSIP (not name substring) is deliberate: an early draft of
# this feature matched the substring "APPLE" against "MAUI LD & PINEAPPLE
# INC" and would have produced a false positive.
TARGET_CUSIPS = {
    "AAPL": "037833100",
    "MSFT": "594918104",
    "NVDA": "67066G104",
    "SPY": "78462F103",   # SPDR S&P 500 ETF Trust
}

CONGRESS_LOOKBACK_DAYS = 90  # see docstring: 30d missed real matches in research, 90d balances freshness vs Congress's slow/uneven filing lag
CONVICTION_CACHE_PATH = Path(__file__).with_name("alpaca_stocks_conviction_cache.json")
CONVICTION_CACHE_MAX_AGE_SEC = 12 * 3600  # multi-day/week-lag signals; no value in refetching every 5-min cycle

LEDGER_PATH = Path(__file__).with_name("alpaca_stocks_ledger.json")
LEDGER_LOCK = threading.Lock()

USD_PER_TRADE = 500.0          # fixed size per position, small vs $100k paper cash
MAX_CONCURRENT_POSITIONS = len(UNIVERSE)   # at most one position per symbol
MAX_DECISIONS_KEPT = 500        # cap the decisions log so the ledger file doesn't grow forever

_alpaca_stocks_running = False


# ---------------------------------------------------------------------- #
# Conviction overlay: 13F + Congress. Fail-open at every layer -- see the
# "CONVICTION OVERLAY" section of the module docstring for the exact rule.
# ---------------------------------------------------------------------- #
def _compute_13f_directions() -> dict:
    """Returns {symbol: -1/0/+1} across all 4 UNIVERSE tickers in one pass
    (shares the per-filer filings fetch instead of refetching per symbol).
    Any single filer failing (network, malformed filing, etc.) is skipped
    for that filer only -- never raises, never aborts the other filers."""
    counts = {s: 0 for s in UNIVERSE}
    if not _SEC_13F_IMPORT_OK:
        return {s: 0 for s in UNIVERSE}

    for filer_name, cik in sec_13f_signal.CURATED_FILERS.items():
        try:
            filings = sec_13f_signal.get_last_two_filings(cik)
            if len(filings) < 1:
                continue
            curr = filings[0]
            prev = filings[1] if len(filings) > 1 else {"holdings": []}
            diffs = sec_13f_signal.diff_holdings(prev["holdings"], curr["holdings"])
            by_cusip = {d["cusip"]: d for d in diffs}
            for symbol, cusip in TARGET_CUSIPS.items():
                row = by_cusip.get(cusip)
                if not row:
                    continue
                if row["action"] in ("NEW", "INCREASED"):
                    counts[symbol] += 1
                elif row["action"] in ("EXIT", "DECREASED"):
                    counts[symbol] -= 1
        except Exception:
            continue  # this filer's data unavailable this cycle -- fail-open, try the rest

    return {s: (1 if c >= 2 else (-1 if c <= -2 else 0)) for s, c in counts.items()}


def _compute_congress_directions() -> dict:
    """Returns {symbol: -1/0/+1} for all 4 UNIVERSE tickers from House PTR
    disclosures. Fail-open: any error (fetch, parse, Senate NotImplementedError
    if ever called) yields all-zero directions, never raises."""
    directions = {s: 0 for s in UNIVERSE}
    if not _CONGRESS_IMPORT_OK:
        return directions
    try:
        txs = congress_trades_signal.fetch_house_transactions()
        sig = congress_trades_signal.conviction(txs, lookback_days=CONGRESS_LOOKBACK_DAYS)
        by_ticker = {r["ticker"]: r for r in sig["by_ticker"]}
        for symbol in UNIVERSE:
            row = by_ticker.get(symbol)
            if not row or row["unique_filers"] < 2:
                continue  # single-filer disclosures are too noisy for a conviction bonus
            if row["net_buy_pressure"] > 0:
                directions[symbol] = 1
            elif row["net_buy_pressure"] < 0:
                directions[symbol] = -1
    except Exception:
        return {s: 0 for s in UNIVERSE}
    return directions


def _load_conviction_cache() -> Optional[dict]:
    if not CONVICTION_CACHE_PATH.exists():
        return None
    try:
        age = time.time() - CONVICTION_CACHE_PATH.stat().st_mtime
        if age >= CONVICTION_CACHE_MAX_AGE_SEC:
            return None
        with open(CONVICTION_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None  # corrupt/unreadable cache -- fail-open, recompute


def _save_conviction_cache(data: dict) -> None:
    try:
        tmp = CONVICTION_CACHE_PATH.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        tmp.replace(CONVICTION_CACHE_PATH)
    except Exception:
        pass  # caching is an optimization only -- never let it break the cycle


def get_conviction_bonuses(send_fn=None) -> dict:
    """Returns {symbol: {"bonus": -1/0/+1, "13f_direction": int, "congress_direction": int}}
    for every symbol in UNIVERSE, disk-cached for CONVICTION_CACHE_MAX_AGE_SEC.
    Never raises -- any failure anywhere in this function degrades to an
    all-zero-bonus dict so callers can add it to the technical score
    unconditionally."""
    cached = _load_conviction_cache()
    if cached is not None:
        return cached.get("by_symbol", {s: {"bonus": 0, "13f_direction": 0, "congress_direction": 0} for s in UNIVERSE})

    try:
        thirteenf_dirs = _compute_13f_directions()
    except Exception as e:
        _safe_send(send_fn, f"[ALPACA-STOCKS] 13F conviction overlay failed (non-blocking): {type(e).__name__}: {e}")
        thirteenf_dirs = {s: 0 for s in UNIVERSE}

    try:
        congress_dirs = _compute_congress_directions()
    except Exception as e:
        _safe_send(send_fn, f"[ALPACA-STOCKS] Congress conviction overlay failed (non-blocking): {type(e).__name__}: {e}")
        congress_dirs = {s: 0 for s in UNIVERSE}

    by_symbol = {}
    for s in UNIVERSE:
        d13 = thirteenf_dirs.get(s, 0)
        dc = congress_dirs.get(s, 0)
        bonus = max(-1, min(1, d13 + dc))
        by_symbol[s] = {"bonus": bonus, "13f_direction": d13, "congress_direction": dc}

    _save_conviction_cache({"computed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "by_symbol": by_symbol})
    return by_symbol


# ---------------------------------------------------------------------- #
# Ledger (own JSON file, separate from crypto sim_portfolio_v7.json)
# ---------------------------------------------------------------------- #
def _default_ledger() -> dict:
    return {"positions": {}, "decisions": [], "closed_trades": [], "last_run": None}


def load_ledger() -> dict:
    with LEDGER_LOCK:
        if not LEDGER_PATH.exists():
            return _default_ledger()
        try:
            with open(LEDGER_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, default in _default_ledger().items():
                data.setdefault(key, default)
            return data
        except Exception:
            # Corrupt/partial file -- never crash the sleeve over it, start fresh
            # in memory (existing file on disk is left untouched until next save).
            return _default_ledger()


def save_ledger(ledger: dict) -> None:
    with LEDGER_LOCK:
        tmp_path = LEDGER_PATH.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(ledger, f, indent=2, default=str)
        tmp_path.replace(LEDGER_PATH)


# ---------------------------------------------------------------------- #
# Scoring -- simple, documented, see module docstring
# ---------------------------------------------------------------------- #
def score_symbol(closes: list) -> tuple:
    """Returns (score:int in [-2,2], detail:dict). `closes` oldest-first."""
    if len(closes) < 30:
        return 0, {"reason": f"insufficient data ({len(closes)} closes < 30)"}

    sma10 = sum(closes[-10:]) / 10
    sma30 = sum(closes[-30:]) / 30
    trend_score = 1 if sma10 > sma30 else -1

    ret_5d = (closes[-1] - closes[-6]) / closes[-6] if len(closes) >= 6 else 0.0
    if ret_5d > 0.01:
        momentum_score = 1
    elif ret_5d < -0.01:
        momentum_score = -1
    else:
        momentum_score = 0

    score = trend_score + momentum_score
    detail = {
        "sma10": round(sma10, 2), "sma30": round(sma30, 2),
        "ret_5d_pct": round(ret_5d * 100, 2),
        "trend_score": trend_score, "momentum_score": momentum_score,
        "last_close": round(closes[-1], 2),
    }
    return score, detail


def decide(score: int) -> str:
    if score >= 2:
        return "BUY"
    if score <= -2:
        return "SELL"
    return "HOLD"


# ---------------------------------------------------------------------- #
# Cycle -- mirrors run_hl_copytrade_cycle's thin-trigger + background-thread
# pattern in bot.py so this never blocks the main crypto loop.
# ---------------------------------------------------------------------- #
def run_alpaca_stocks_cycle(send_fn) -> None:
    global _alpaca_stocks_running
    if _alpaca_stocks_running:
        return
    _alpaca_stocks_running = True
    threading.Thread(target=_alpaca_stocks_worker, args=(send_fn,), daemon=True).start()


def _alpaca_stocks_worker(send_fn) -> None:
    global _alpaca_stocks_running
    try:
        _alpaca_stocks_worker_body(send_fn)
    except Exception as e:
        _safe_send(send_fn, f"[ALPACA-STOCKS] Worker error: {type(e).__name__}: {e}")
    finally:
        _alpaca_stocks_running = False


def _safe_send(send_fn, msg: str) -> None:
    try:
        if send_fn:
            send_fn(msg)
    except Exception:
        pass


def _alpaca_stocks_worker_body(send_fn) -> None:
    ledger = load_ledger()
    market_open = False
    try:
        market_open = alpaca_broker.is_market_open()
    except Exception as e:
        _safe_send(send_fn, f"[ALPACA-STOCKS] Clock check failed (non-blocking): {type(e).__name__}: {e}")

    # Fail-open by construction: get_conviction_bonuses() never raises and
    # never returns anything but a per-symbol dict, defaulting to bonus=0
    # (technical-only scoring) on any error. See module docstring.
    try:
        conviction_bonuses = get_conviction_bonuses(send_fn)
    except Exception as e:
        _safe_send(send_fn, f"[ALPACA-STOCKS] Conviction overlay unavailable this cycle (non-blocking): {type(e).__name__}: {e}")
        conviction_bonuses = {s: {"bonus": 0, "13f_direction": 0, "congress_direction": 0} for s in UNIVERSE}

    for symbol in UNIVERSE:
        try:
            _process_symbol(symbol, ledger, market_open, send_fn, conviction_bonuses.get(symbol, {"bonus": 0}))
        except Exception as e:
            _safe_send(send_fn, f"[ALPACA-STOCKS] {symbol} error (isolated, skipped): {type(e).__name__}: {e}")

    ledger["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if len(ledger["decisions"]) > MAX_DECISIONS_KEPT:
        ledger["decisions"] = ledger["decisions"][-MAX_DECISIONS_KEPT:]
    save_ledger(ledger)


def _process_symbol(symbol: str, ledger: dict, market_open: bool, send_fn, conviction: Optional[dict] = None) -> None:
    closes = alpaca_broker.get_recent_closes(symbol)
    technical_score, detail = score_symbol(closes)

    # Additive conviction overlay (see module docstring "CONVICTION
    # OVERLAY"): -1/0/+1, capped so it can never fire a trade by itself,
    # only tip an already-borderline technical score. Defaults to 0
    # (pure technical, unchanged behavior) if the overlay wasn't computed.
    conviction = conviction or {"bonus": 0}
    conviction_bonus = conviction.get("bonus", 0)
    score = technical_score + conviction_bonus
    action = decide(score)

    decision_record = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol, "score": score, "technical_score": technical_score,
        "conviction_bonus": conviction_bonus,
        "13f_direction": conviction.get("13f_direction", 0),
        "congress_direction": conviction.get("congress_direction", 0),
        "action": action, "market_open": market_open, **detail,
    }
    ledger["decisions"].append(decision_record)

    has_position = symbol in ledger["positions"]

    if action == "BUY" and not has_position:
        open_count = len(ledger["positions"])
        if open_count >= MAX_CONCURRENT_POSITIONS:
            return
        if not market_open:
            return  # scored + logged above; no order while market is closed
        last_close = detail.get("last_close") or (closes[-1] if closes else None)
        if not last_close or last_close <= 0:
            return
        qty = round(USD_PER_TRADE / last_close, 4)
        if qty <= 0:
            return
        order = alpaca_broker.submit_market_order(symbol, "buy", qty)
        entry_price = order.get("filled_avg_price") or last_close
        ledger["positions"][symbol] = {
            "qty": qty, "entry_price": entry_price,
            "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "order_id": order.get("id"), "score_at_entry": score,
        }
        _safe_send(send_fn, f"[ALPACA-STOCKS] BUY {symbol} qty={qty} ~${entry_price:.2f} (score={score})")

    elif action == "SELL" and has_position:
        if not market_open:
            return
        pos = ledger["positions"][symbol]
        order = alpaca_broker.submit_market_order(symbol, "sell", pos["qty"])
        exit_price = order.get("filled_avg_price") or detail.get("last_close")
        pnl = None
        if exit_price and pos.get("entry_price"):
            pnl = round((exit_price - pos["entry_price"]) * pos["qty"], 2)
        ledger["closed_trades"].append({
            **pos, "symbol": symbol, "exit_price": exit_price, "pnl": pnl,
            "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "exit_order_id": order.get("id"),
        })
        del ledger["positions"][symbol]
        _safe_send(send_fn, f"[ALPACA-STOCKS] SELL {symbol} qty={pos['qty']} pnl=${pnl} (score={score})")


def get_alpaca_stocks_summary() -> dict:
    """Reporting-only helper (not wired into bot.py's Telegram status --
    kept out of scope for this change). Paper account equity comes straight
    from Alpaca (get_account_balance), never mixed with the crypto sim
    totals."""
    ledger = load_ledger()
    try:
        account = alpaca_broker.get_account_balance()
    except Exception:
        account = {}
    return {
        "account": account,
        "open_positions": ledger["positions"],
        "decisions_logged": len(ledger["decisions"]),
        "closed_trades": len(ledger["closed_trades"]),
        "last_run": ledger.get("last_run"),
    }


if __name__ == "__main__":
    # Standalone smoke test: one cycle, synchronous (bypass the thread/flag
    # so this script's own print output can wait for completion).
    def _print_send(msg):
        print(msg)
    _alpaca_stocks_worker_body(_print_send)
    print("[alpaca_stocks_sleeve] summary:", get_alpaca_stocks_summary())
