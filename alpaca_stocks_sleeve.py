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

DYNAMIC COPY UNIVERSE (added 2026-11-04, additive only): the fixed 4-ticker
  UNIVERSE above only ever scores AAPL/MSFT/NVDA/SPY, no matter what a
  curated filer actually holds. get_dynamic_universe() below uses
  sec_13f_signal.resolve_top_weighted() (OpenFIGI CUSIP->ticker resolution,
  added the same day) to pull each of COPY_FILER_NAMES's current top
  COPY_TOP_N holdings by value and add whichever of those tickers aren't
  already in UNIVERSE to the symbols scored this cycle -- up to
  MAX_DYNAMIC_SYMBOLS of them, so the universe can grow with what the
  copied filers actually hold without becoming unbounded. A
  dynamically-added symbol is scored and traded through the exact same
  path as the original 4 (same score_symbol, same $500 USD_PER_TRADE, same
  decide() thresholds, same conviction overlay -- its CUSIP is merged into
  the overlay's lookup map so _compute_13f_directions sees it like any
  curated ticker). Nothing about the original 4 tickers' behavior changes
  if this fails or returns nothing.

  Widened 2026-11-04 (same day, later -- per explicit user instruction to
  cut the crypto sleeves and make this the bot's primary strategy):
  COPY_FILER_NAMES now pulls from every one of the 7 CURATED_FILERS
  (Berkshire, Scion/Burry, Bridgewater/Dalio, Duquesne/Druckenmiller,
  Pershing Square/Ackman, Third Point/Loeb, Renaissance) instead of just
  Berkshire, ranked by (number of filers holding it) then (summed
  weight_pct across filers) so a name several of these managers converge
  on outranks one held by only one of them. MAX_DYNAMIC_SYMBOLS raised
  3 -> 15 to match -- still a hard cap, "widen" does not mean unbounded;
  see get_dynamic_universe()'s docstring for the exact ranking rule.

  Cadence: disk-cached (own file, COPY_CACHE_PATH) for COPY_CACHE_MAX_AGE
  (7 days) -- 13F only updates quarterly, no value refreshing more often.
  Fail-open exactly like the conviction overlay: any error (one filer's
  SEC fetch, an OpenFIGI lookup, a corrupt cache) degrades that filer's
  contribution to empty and never raises; a total failure degrades to an
  empty dynamic universe, and the cycle proceeds with just the original 4
  tickers, unchanged from before this feature existed.
"""
from __future__ import annotations

import json
import os
from agent_events import log_event  # decision journal, never raises
import agency  # SeniorExpert final gate (agence step 1)
import trade_manager  # rules R1-R10 on open positions
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import alpaca_broker

# Reuse the bot's shared logger config (stdout + rotating file handler) so
# this sleeve's cycles show up in the same log stream as everything else.
# logging_config.py is a standalone, dependency-free module (logging/os/sys
# only, no bot.py import) so pulling it in here does not break this file's
# "no bot.py coupling" discipline -- see module docstring.
#
# ROOT CAUSE (2026-09-09 investigation): run_alpaca_stocks_cycle() IS wired
# correctly into the live trading_loop() in bot.py (same loop that logs
# "Trading Loop autonome V8 demarre", not a separate/dead loop as first
# suspected) and DOES fire every CYCLE_ALPACA_STOCKS (300s). But before this
# fix, the only output on a HOLD decision (the overwhelming majority of
# cycles -- score_symbol() needs |score|>=2 to even consider a trade) was a
# silent append to the ledger JSON file. _safe_send()/send_fn only reaches
# Telegram (bot.py's `send()`), never the logger -- so a HOLD, or a
# would-be BUY/SELL skipped because the market is closed, produced zero
# lines in trading_bot.log / stdout. That looked identical to "the cycle
# never runs" from the logs alone. Fix: log a one-line cycle summary here
# every run, independent of whether any order was placed.
try:
    from logging_config import logger
except Exception:  # pragma: no cover - keep this sleeve importable standalone
    import logging
    logger = logging.getLogger("alpaca_stocks_sleeve")

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

# Optional social/news risk check (added 2026-11-04, per explicit user
# instruction: label the risk on every BUY, never gate on it). Same
# fail-open import contract as the two signals above.
try:
    import stock_sentiment
    _STOCK_SENTIMENT_IMPORT_OK = True
except Exception:
    stock_sentiment = None
    _STOCK_SENTIMENT_IMPORT_OK = False

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

# PERSISTENCE FIX (2026-11-04): these 3 files used to be Path(__file__).with_name(...)
# -- i.e. written next to this source file, inside /workspace in the
# container, which is NOT the bind-mounted volume (only /opt/trading-bot/data
# -> /app/data is, same DATA_DIR pattern bot.py's own DATA_FILE already
# uses for sim_portfolio_v7.json). Every full container recreate (not just
# a restart) rebuilds /workspace from the image, silently resetting this
# sleeve's ledger -- i.e. losing its memory of which positions it already
# holds -- discovered when today's redeploy showed open_positions=0 right
# after a position had been confirmed open moments before. Moving these
# under DATA_DIR makes them survive a redeploy exactly like the crypto
# sleeves' state already does. Falls back to "next to this file" if
# DATA_DIR isn't set (e.g. local dev without the env var), unchanged from
# before for that case.
_DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).parent)
CONVICTION_CACHE_PATH = _DATA_DIR / "alpaca_stocks_conviction_cache.json"
CONVICTION_CACHE_MAX_AGE_SEC = 12 * 3600  # multi-day/week-lag signals; no value in refetching every 5-min cycle

# Dynamic copy universe -- see module docstring "DYNAMIC COPY UNIVERSE".
COPY_FILER_NAMES = None   # None = every key in sec_13f_signal.CURATED_FILERS; or pass an explicit list to narrow it
COPY_TOP_N = 5                           # how many of EACH filer's biggest holdings to consider
MAX_DYNAMIC_SYMBOLS = 15                 # cap on how many NEW tickers this can add to the scored universe
COPY_CACHE_PATH = _DATA_DIR / "alpaca_stocks_copy_cache.json"
COPY_CACHE_MAX_AGE_SEC = 7 * 24 * 3600    # 13F is quarterly -- weekly refresh is already generous

LEDGER_PATH = _DATA_DIR / "alpaca_stocks_ledger.json"
LEDGER_LOCK = threading.Lock()

USD_PER_TRADE = 250.0          # legacy fixed size, replaced by POS_PCT below
# 2026-11-05 user request (paper): ~100 % invested, spread at 5 % of equity per position. While less than
# FILL_TARGET is invested, score 1 (the best below the normal BUY threshold of 2) also buys, best first.
POS_PCT = float(os.environ.get("ALPACA_POS_PCT", 0.05))
FILL_TARGET = float(os.environ.get("ALPACA_FILL_TARGET", 0.95))
MAX_DECISIONS_KEPT = 500        # cap the decisions log so the ledger file doesn't grow forever

# VIRTUAL PORTFOLIO (added 2026-11-04, per explicit user instruction: a
# real $1,000 starting bankroll, mirroring the crypto HL-copy sleeve's own
# isolated "cash" ledger field, tracked and reported SEPARATELY from the
# real Alpaca account balance. Alpaca's own paper account can't be reset
# to a custom figure via its API -- only to its default $100k -- so real
# orders still execute against that real (much larger) account, but
# position-sizing and every "how much do we have" report use this
# self-tracked virtual_cash instead, exactly the way the crypto ledger's
# "cash" already works. USD_PER_TRADE=$500 means at most ~2 positions can
# ever be funded out of $1,000 -- a real, known consequence of keeping
# USD_PER_TRADE unchanged rather than something hidden.
VIRTUAL_STARTING_CASH = 1000.0

_alpaca_stocks_running = False


# ---------------------------------------------------------------------- #
# Conviction overlay: 13F + Congress. Fail-open at every layer -- see the
# "CONVICTION OVERLAY" section of the module docstring for the exact rule.
# ---------------------------------------------------------------------- #
def _compute_13f_directions(universe: list, cusip_map: dict) -> dict:
    """Returns {symbol: -1/0/+1} across every ticker in `universe` in one
    pass (shares the per-filer filings fetch instead of refetching per
    symbol). `cusip_map` lets dynamically-added copy-universe tickers (see
    get_dynamic_universe) be matched the same way as the hand-curated
    TARGET_CUSIPS. Any single filer failing (network, malformed filing,
    etc.) is skipped for that filer only -- never raises, never aborts the
    other filers."""
    counts = {s: 0 for s in universe}
    if not _SEC_13F_IMPORT_OK:
        return counts

    for filer_name, cik in sec_13f_signal.CURATED_FILERS.items():
        try:
            filings = sec_13f_signal.get_last_two_filings(cik)
            if len(filings) < 1:
                continue
            curr = filings[0]
            prev = filings[1] if len(filings) > 1 else {"holdings": []}
            diffs = sec_13f_signal.diff_holdings(prev["holdings"], curr["holdings"])
            by_cusip = {d["cusip"]: d for d in diffs}
            for symbol in universe:
                cusip = cusip_map.get(symbol)
                if not cusip:
                    continue
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


def _compute_congress_directions(universe: list) -> dict:
    """Returns {symbol: -1/0/+1} for every ticker in `universe` from House
    PTR disclosures. Fail-open: any error (fetch, parse, Senate
    NotImplementedError if ever called) yields all-zero directions, never
    raises."""
    directions = {s: 0 for s in universe}
    if not _CONGRESS_IMPORT_OK:
        return directions
    try:
        txs = congress_trades_signal.fetch_house_transactions()
        sig = congress_trades_signal.conviction(txs, lookback_days=CONGRESS_LOOKBACK_DAYS)
        by_ticker = {r["ticker"]: r for r in sig["by_ticker"]}
        for symbol in universe:
            row = by_ticker.get(symbol)
            if not row or row["unique_filers"] < 2:
                continue  # single-filer disclosures are too noisy for a conviction bonus
            if row["net_buy_pressure"] > 0:
                directions[symbol] = 1
            elif row["net_buy_pressure"] < 0:
                directions[symbol] = -1
    except Exception:
        return {s: 0 for s in universe}
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


def get_conviction_bonuses(universe: list, cusip_map: dict, send_fn=None) -> dict:
    """Returns {symbol: {"bonus": -1/0/+1, "13f_direction": int, "congress_direction": int}}
    for every symbol in `universe`, disk-cached for CONVICTION_CACHE_MAX_AGE_SEC.
    Never raises -- any failure anywhere in this function degrades to an
    all-zero-bonus dict so callers can add it to the technical score
    unconditionally."""
    default = {s: {"bonus": 0, "13f_direction": 0, "congress_direction": 0} for s in universe}
    cached = _load_conviction_cache()
    if cached is not None:
        by_symbol = cached.get("by_symbol", {})
        # Cache may predate a symbol that just entered the dynamic universe
        # (or no longer cover one that left it) -- fill gaps, don't refetch.
        return {s: by_symbol.get(s, default[s]) for s in universe}

    try:
        thirteenf_dirs = _compute_13f_directions(universe, cusip_map)
    except Exception as e:
        _safe_send(send_fn, f"[ALPACA-STOCKS] 13F conviction overlay failed (non-blocking): {type(e).__name__}: {e}")
        thirteenf_dirs = {s: 0 for s in universe}

    try:
        congress_dirs = _compute_congress_directions(universe)
    except Exception as e:
        _safe_send(send_fn, f"[ALPACA-STOCKS] Congress conviction overlay failed (non-blocking): {type(e).__name__}: {e}")
        congress_dirs = {s: 0 for s in universe}

    by_symbol = {}
    for s in universe:
        d13 = thirteenf_dirs.get(s, 0)
        dc = congress_dirs.get(s, 0)
        bonus = max(-1, min(1, d13 + dc))
        by_symbol[s] = {"bonus": bonus, "13f_direction": d13, "congress_direction": dc}

    _save_conviction_cache({"computed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "by_symbol": by_symbol})
    return by_symbol


# ---------------------------------------------------------------------- #
# Dynamic copy universe: 13F top-N weighted holdings -> tickers to add.
# See module docstring "DYNAMIC COPY UNIVERSE". Fail-open at every layer,
# same contract as the conviction overlay above.
# ---------------------------------------------------------------------- #
def _load_copy_cache() -> Optional[dict]:
    if not COPY_CACHE_PATH.exists():
        return None
    try:
        age = time.time() - COPY_CACHE_PATH.stat().st_mtime
        if age >= COPY_CACHE_MAX_AGE_SEC:
            return None
        with open(COPY_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None  # corrupt/unreadable cache -- fail-open, recompute


def _save_copy_cache(data: dict) -> None:
    try:
        tmp = COPY_CACHE_PATH.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        tmp.replace(COPY_CACHE_PATH)
    except Exception:
        pass  # caching is an optimization only -- never let it break the cycle


def get_dynamic_universe(send_fn=None) -> dict:
    """Returns {ticker: cusip} for up to MAX_DYNAMIC_SYMBOLS tickers drawn
    from COPY_FILER_NAMES's (default: every CURATED_FILER) current top
    COPY_TOP_N holdings each, disk-cached for COPY_CACHE_MAX_AGE_SEC.

    Ranking across filers: a ticker's rank key is
    (how many of the queried filers hold it in their top COPY_TOP_N,
     sum of its weight_pct across those filers) -- both descending, so a
    name several managers independently converge on outranks one only one
    of them holds, and within the same filer-count ties break on combined
    conviction size. Already-in-UNIVERSE tickers are excluded before
    ranking so the cap only spends its budget on genuinely new names.

    Never raises -- any failure (missing module, SEC down for one or all
    filers, no CIK for a name, OpenFIGI unavailable, unresolved tickers)
    degrades that filer's contribution to nothing; a total failure
    degrades to {}, meaning the sleeve scores exactly the original 4
    tickers, unchanged."""
    cached = _load_copy_cache()
    if cached is not None:
        return cached.get("dynamic_symbols", {})

    result: dict = {}
    try:
        if not _SEC_13F_IMPORT_OK:
            raise RuntimeError("sec_13f_signal not importable")
        filer_names = COPY_FILER_NAMES or list(sec_13f_signal.CURATED_FILERS.keys())

        # {ticker: {"cusip": str, "filer_count": int, "total_weight": float}}
        candidates: dict = {}
        for filer_name in filer_names:
            cik = sec_13f_signal.CURATED_FILERS.get(filer_name)
            if not cik:
                continue  # unknown filer name -- skip it, never abort the others
            try:
                top = sec_13f_signal.resolve_top_weighted(cik, top_n=COPY_TOP_N)
            except Exception:
                continue  # this filer's 13F unavailable this cycle -- fail-open, try the rest
            for row in top:
                ticker = row.get("ticker")
                if not ticker or ticker in UNIVERSE:
                    continue
                entry = candidates.setdefault(ticker, {"cusip": row["cusip"], "filer_count": 0, "total_weight": 0.0})
                entry["filer_count"] += 1
                entry["total_weight"] += row.get("weight_pct", 0.0)

        ranked = sorted(candidates.items(), key=lambda kv: (kv[1]["filer_count"], kv[1]["total_weight"]), reverse=True)
        for ticker, entry in ranked[:MAX_DYNAMIC_SYMBOLS]:
            result[ticker] = entry["cusip"]
    except Exception as e:
        _safe_send(send_fn, f"[ALPACA-STOCKS] Dynamic copy universe unavailable this cycle (non-blocking): {type(e).__name__}: {e}")
        result = {}

    _save_copy_cache({"computed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "dynamic_symbols": result})
    return result


# ---------------------------------------------------------------------- #
# Ledger (own JSON file, separate from crypto sim_portfolio_v7.json)
# ---------------------------------------------------------------------- #
def _default_ledger() -> dict:
    return {"positions": {}, "decisions": [], "closed_trades": [], "last_run": None,
            "virtual_cash": VIRTUAL_STARTING_CASH}


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

    # Fail-open by construction: get_dynamic_universe() never raises and
    # never returns anything but a dict, defaulting to {} (original 4
    # tickers only) on any error. See module docstring "DYNAMIC COPY
    # UNIVERSE".
    try:
        dynamic_cusips = get_dynamic_universe(send_fn)
    except Exception as e:
        _safe_send(send_fn, f"[ALPACA-STOCKS] Dynamic copy universe unavailable this cycle (non-blocking): {type(e).__name__}: {e}")
        dynamic_cusips = {}
    effective_universe = UNIVERSE + [s for s in dynamic_cusips if s not in UNIVERSE]
    combined_cusips = {**TARGET_CUSIPS, **dynamic_cusips}
    if dynamic_cusips:
        logger.info(f"[ALPACA-STOCKS] Dynamic copy universe this cycle: {dynamic_cusips}")

    # Fail-open by construction: get_conviction_bonuses() never raises and
    # never returns anything but a per-symbol dict, defaulting to bonus=0
    # (technical-only scoring) on any error. See module docstring.
    try:
        conviction_bonuses = get_conviction_bonuses(effective_universe, combined_cusips, send_fn)
    except Exception as e:
        _safe_send(send_fn, f"[ALPACA-STOCKS] Conviction overlay unavailable this cycle (non-blocking): {type(e).__name__}: {e}")
        conviction_bonuses = {s: {"bonus": 0, "13f_direction": 0, "congress_direction": 0} for s in effective_universe}

    # Portfolio-level circuit breaker (added 2026-11-04) -- checked once per
    # cycle against the virtual $1,000 bankroll, not the real (much larger)
    # Alpaca balance. Blocks new BUYs only this cycle; SELLs (closing a
    # position) are never blocked -- see check_drawdown_guard's docstring.
    virtual_equity = ledger.get("virtual_cash", VIRTUAL_STARTING_CASH) + sum(
        p.get("qty", 0) * p.get("entry_price", 0) for p in ledger["positions"].values()
    )
    dd = check_drawdown_guard(ledger, virtual_equity)
    trading_blocked = dd["blocked"]
    if trading_blocked:
        log_event("DrawdownGuard", "actions_us", "VETO", "", dd.get("reason") or "", result="aucun achat ce cycle")
        logger.info(f"[ALPACA-STOCKS] Circuit breaker actif: {dd['reason']}")

    max_positions = max(len(effective_universe), int(1 / POS_PCT))
    invested = sum(float(p.get("cost", 0)) for p in ledger["positions"].values())
    ledger["_fill_mode"] = invested < FILL_TARGET * max(1.0, virtual_equity)
    # best-scored candidates first when filling: order the universe by last cycle's scores
    last = {d.get("symbol"): d.get("score", 0) for d in ledger.get("decisions", [])[-200:] if isinstance(d, dict)}
    effective_universe = sorted(effective_universe, key=lambda s: -(last.get(s) or 0))
    cycle_summary = []
    for symbol in effective_universe:
        try:
            result = _process_symbol(symbol, ledger, market_open, send_fn, conviction_bonuses.get(symbol, {"bonus": 0}), max_positions, trading_blocked)
            cycle_summary.append(f"{symbol}={result}")
        except Exception as e:
            _safe_send(send_fn, f"[ALPACA-STOCKS] {symbol} error (isolated, skipped): {type(e).__name__}: {e}")
            cycle_summary.append(f"{symbol}=ERROR({type(e).__name__})")

    if market_open:  # orders only go through while the market is open
        try:
            _stocks_trade_manager(ledger, send_fn)
        except Exception as e:
            _safe_send(send_fn, f"[ALPACA-STOCKS] TradeManager error (non-blocking): {type(e).__name__}: {e}")

    ledger.pop("_fill_mode", None)
    ledger["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if len(ledger["decisions"]) > MAX_DECISIONS_KEPT:
        ledger["decisions"] = ledger["decisions"][-MAX_DECISIONS_KEPT:]
    save_ledger(ledger)

    # ALWAYS log a cycle heartbeat, independent of BUY/SELL/HOLD -- see the
    # ROOT CAUSE note above the logger import. This is the only change that
    # makes a normal (HOLD-only, or market-closed) cycle visible in the logs.
    logger.info(
        f"[ALPACA-STOCKS] Cycle done | market_open={market_open} | "
        f"open_positions={len(ledger['positions'])} | " + ", ".join(cycle_summary)
    )


# ---------------------------------------------------------------------- #
# Portfolio-level risk guards (added 2026-11-04, per explicit user
# instruction after reviewing agents/drawdown_guard_agent.py and
# agents/correlation_watcher_agent.py for reuse). Deliberately NOT
# importing those agents directly -- this module's whole design is
# self-contained/no-bot.py-coupling (see module docstring), and
# agents.base_agent pulls in a ChromaDB-backed KnowledgeBase, a much
# heavier dependency than this sleeve otherwise needs. These are
# lightweight, standalone equivalents of the same two ideas instead:
#   - Drawdown guard: persisted in the ledger (ledger["risk_state"]), NOT
#     in-memory like the crypto side's agent instance -- this actually
#     makes it MORE robust than the crypto version, since it survives a
#     container recreate instead of losing its loss-streak memory.
#   - Correlation watcher: Pearson correlation on the same closes data
#     already fetched per symbol (alpaca_broker.get_recent_closes), no
#     extra network calls beyond what each cycle already makes.
# Both are informational/sizing-only except the drawdown guard's full
# stop, which blocks NEW buys only -- never blocks a SELL (closing a
# position is risk-reducing, never something to veto).
# ---------------------------------------------------------------------- #
MAX_DAILY_DD_PCT = 0.12     # -12%/day = block new buys for the rest of the day
MAX_OVERALL_DD_PCT = 0.20   # -20% from peak = block new buys for 12h
CORR_HIGH_THRESHOLD = 0.80
CORR_VERY_HIGH = 0.90
REDUCTION_HIGH = 0.40
REDUCTION_VERY_HIGH = 0.65


def _default_risk_state() -> dict:
    return {"consecutive_losses": 0, "pause_until": None, "peak_equity": 0.0,
            "daily_start_equity": 0.0, "daily_start_date": None}


def check_drawdown_guard(ledger: dict, equity: float) -> dict:
    """Portfolio-level circuit breaker. Returns {"blocked": bool, "reason":
    str|None}. Never raises -- any error degrades to not-blocked (fail-open,
    same contract as every other overlay in this file) since a guard that
    can silently disable itself is safer than one that can silently freeze
    all trading forever on an unrelated bug."""
    try:
        state = ledger.setdefault("risk_state", _default_risk_state())
        today = datetime.now().strftime("%Y-%m-%d")
        if state.get("daily_start_date") != today:
            state["daily_start_equity"] = equity
            state["daily_start_date"] = today
        if equity > state.get("peak_equity", 0):
            state["peak_equity"] = equity

        now = time.time()
        if state.get("pause_until") and now < state["pause_until"]:
            remaining = int((state["pause_until"] - now) / 60)
            return {"blocked": True, "reason": f"pause active ({remaining}min restantes)"}
        state["pause_until"] = None  # auto-resume once the pause has elapsed

        daily_start = state.get("daily_start_equity") or equity
        peak = state.get("peak_equity") or equity
        dd_daily = (equity - daily_start) / daily_start if daily_start else 0.0
        dd_overall = (equity - peak) / peak if peak else 0.0

        if dd_daily <= -MAX_DAILY_DD_PCT:
            state["pause_until"] = now + 4 * 3600
            return {"blocked": True, "reason": f"drawdown journalier {dd_daily:.1%}"}
        if dd_overall <= -MAX_OVERALL_DD_PCT:
            state["pause_until"] = now + 12 * 3600
            return {"blocked": True, "reason": f"drawdown total {dd_overall:.1%}"}

        losses = state.get("consecutive_losses", 0)
        if losses >= 8:
            state["pause_until"] = now + 24 * 3600
            return {"blocked": True, "reason": f"{losses} pertes consecutives"}
        if losses >= 5:
            state["pause_until"] = now + 3600
            return {"blocked": True, "reason": f"{losses} pertes consecutives"}
        if losses >= 3:
            state["pause_until"] = now + 900
            return {"blocked": True, "reason": f"{losses} pertes consecutives"}
        return {"blocked": False, "reason": None}
    except Exception:
        return {"blocked": False, "reason": None}


def _reduce_position(ledger: dict, symbol: str, fraction: float, reason: str, price: float, send_fn) -> None:
    """Sell `fraction` (1 = everything) of a position at market, booked like a normal SELL."""
    pos = ledger["positions"].get(symbol)
    if not pos:
        return
    qty = pos["qty"] if fraction >= 0.999 else round(pos["qty"] * fraction, 4)
    if qty <= 0:
        return
    order = alpaca_broker.submit_market_order(symbol, "sell", qty)
    exit_price = order.get("filled_avg_price") or price
    pnl = round((exit_price - pos["entry_price"]) * qty, 2) if exit_price and pos.get("entry_price") else None
    part_cost = round(pos.get("cost", qty * pos["entry_price"]) * qty / pos["qty"], 2)
    ledger["closed_trades"].append({**{k: v for k, v in pos.items() if k != "management_log"}, "symbol": symbol,
                                    "qty": qty, "cost": part_cost, "exit_price": exit_price, "pnl": pnl,
                                    "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    "exit_order_id": order.get("id"), "exit_reason": reason,
                                    "partial": fraction < 0.999})
    ledger["virtual_cash"] = round(float(ledger.get("virtual_cash", VIRTUAL_STARTING_CASH)) + part_cost + (pnl or 0), 2)
    if fraction >= 0.999:
        del ledger["positions"][symbol]
    else:
        pos["qty"] = round(pos["qty"] - qty, 4)
        pos["cost"] = round(pos.get("cost", 0) - part_cost, 2)
    _record_trade_outcome(ledger, pnl)
    _safe_send(send_fn, f"[ALPACA-STOCKS] {'SELL' if fraction >= 0.999 else 'REDUCE'} {symbol} qty={qty} pnl=${pnl} | {reason}")
    log_event("Executor", "actions_us", "CLOSE" if fraction >= 0.999 else "REDUCE", symbol, reason, result=pnl,
              price=exit_price, fraction=round(fraction, 3))


def _stocks_trade_manager(ledger: dict, send_fn) -> None:
    views = []
    for symbol, pos in ledger["positions"].items():
        bars = alpaca_broker.get_recent_bars(symbol, "15m", 3)
        if not bars or not pos.get("entry_price"):
            continue
        try:
            opened = datetime.strptime(pos.get("entry_time", ""), "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            opened = time.time()
        import chart_analyst
        sup, atr = chart_analyst.nearest_support(symbol, "1d")
        views.append({"symbol": symbol, "side": "LONG", "entry": pos["entry_price"], "price": bars[-1][4],
                      "opened_at": opened, "kind": "stocks", "state": pos, "support": sup, "atr": atr,
                      "resistance": chart_analyst.first_resistance(symbol, "1d")})
    trade_manager.manage("actions_us", views,
                         lambda v, rule, action, frac, why: _reduce_position(ledger, v["symbol"], frac, why, v["price"], send_fn),
                         send_fn)


def _record_trade_outcome(ledger: dict, pnl: Optional[float]) -> None:
    """Updates the consecutive-loss streak after a position closes. Never
    raises -- a bookkeeping failure here must never block/break the sleeve."""
    try:
        state = ledger.setdefault("risk_state", _default_risk_state())
        if pnl is None:
            return
        if pnl > 0:
            state["consecutive_losses"] = max(0, state.get("consecutive_losses", 0) - 1)
        elif pnl < 0:
            state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
    except Exception:
        pass


def _returns_from_closes(closes: list) -> list:
    if not closes or len(closes) < 6:
        return []
    return [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1]]


def _pearson(x: list, y: list) -> float:
    n = min(len(x), len(y))
    if n < 5:
        return 0.0
    x, y = x[:n], y[:n]
    mean_x, mean_y = sum(x) / n, sum(y) / n
    num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    den_x = sum((xi - mean_x) ** 2 for xi in x) ** 0.5
    den_y = sum((yi - mean_y) ** 2 for yi in y) ** 0.5
    return (num / (den_x * den_y)) if den_x * den_y else 0.0


def compute_correlation_reduction(symbol: str, closes: list, open_positions: dict) -> float:
    """Returns a size-reduction fraction in [0, 0.65] -- 0 means "no
    reduction needed". Fails open to 0.0 (no reduction) on any error,
    never raises, never blocks a BUY entirely (same "label the risk,
    don't gate on it" principle the social/news risk check already uses)."""
    try:
        if not open_positions:
            return 0.0
        new_returns = _returns_from_closes(closes)
        if not new_returns:
            return 0.0
        corrs = []
        for other_symbol in open_positions:
            if other_symbol == symbol:
                continue
            other_closes = alpaca_broker.get_recent_closes(other_symbol)
            other_returns = _returns_from_closes(other_closes)
            if other_returns:
                corrs.append(_pearson(new_returns, other_returns))
        if not corrs:
            return 0.0
        max_corr = max(corrs)
        if max_corr >= CORR_VERY_HIGH:
            return REDUCTION_VERY_HIGH
        if max_corr >= CORR_HIGH_THRESHOLD:
            return REDUCTION_HIGH
        return 0.0
    except Exception:
        return 0.0


def _compute_entry_risk(symbol: str) -> dict:
    """Non-blocking social/news risk check before a BUY actually fires
    (added 2026-11-04, per explicit user instruction: label the risk,
    never gate on it). See stock_sentiment.py's module docstring for the
    sources (Yahoo RSS + Reddit + X-via-Nitter) and risk_pct semantics --
    same shape as bot.py's compute_crypto_risk_pct on the crypto side.
    Fail-open: any error (module unavailable, network, timeout) yields
    risk_pct=50 (unknown/neutral), never raises, never blocks the order
    that's already decided to fire by the time this is called."""
    if not _STOCK_SENTIMENT_IMPORT_OK:
        return {"risk_pct": 50, "top_headline": None}
    try:
        return stock_sentiment.compute_risk_pct(symbol, "BUY")
    except Exception:
        return {"risk_pct": 50, "top_headline": None}


def _process_symbol(symbol: str, ledger: dict, market_open: bool, send_fn, conviction: Optional[dict] = None, max_positions: int = len(UNIVERSE), trading_blocked: bool = False) -> str:
    """Returns a short status string (e.g. "HOLD score=1", "BUY", "SELL",
    "BUY(skip:market_closed)") describing what happened for this symbol this
    cycle -- used only to build the cycle heartbeat log line in
    _alpaca_stocks_worker_body, never for control flow."""
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

    if action == "HOLD" and not has_position and score >= 1 and ledger.get("_fill_mode"):
        action = "BUY"  # filling toward ~100 % invested with the best candidates below the usual threshold
    if action == "BUY" and not has_position:
        open_count = len(ledger["positions"])
        if open_count >= max_positions:
            return f"BUY(skip:max_positions,score={score})"
        if trading_blocked:
            return f"BUY(skip:circuit_breaker,score={score})"
        if not market_open:
            return f"BUY(skip:market_closed,score={score})"  # scored + logged above; no order while market is closed
        last_close = detail.get("last_close") or (closes[-1] if closes else None)
        if not last_close or last_close <= 0:
            return f"BUY(skip:no_price,score={score})"
        # Virtual bankroll: the real Alpaca paper account holds ~$100k, but this
        # sleeve only ever spends what its own virtual_cash allows.
        virtual_cash = float(ledger.get("virtual_cash", VIRTUAL_STARTING_CASH))
        equity = virtual_cash + sum(float(p.get("cost", 0)) for p in ledger["positions"].values())
        size = equity * POS_PCT
        if virtual_cash < size * 0.5:
            return f"BUY(skip:cash_insuffisant ${virtual_cash:.0f},score={score})"
        reduction = compute_correlation_reduction(symbol, closes, ledger["positions"])
        usd_size = min(size, virtual_cash) * (1 - reduction)
        qty = round(usd_size / last_close, 4)
        if qty <= 0:
            return f"BUY(skip:qty<=0,score={score})"
        # SeniorExpert (agence step 1). Proxy edge while < 100 closed trades: 0.5 % per score point
        # above the BUY threshold (decide(): score >= 2).
        approved, _why = agency.approve_entry("actions_us", symbol, last_close, 0.005 * max(0, score - 1),
                                              {"score": score, "size_reduction": reduction, "side": "LONG"})
        if not approved:
            return f"BUY(veto:senior_expert,score={score})"
        order = alpaca_broker.submit_market_order(symbol, "buy", qty)
        entry_price = order.get("filled_avg_price") or last_close
        cost = round(qty * entry_price, 2)
        ledger["virtual_cash"] = round(virtual_cash - cost, 2)
        risk = _compute_entry_risk(symbol)
        ledger["positions"][symbol] = {
            "qty": qty, "entry_price": entry_price, "cost": cost, "ref_price": last_close,
            "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "order_id": order.get("id"), "score_at_entry": score,
            "risk_pct_at_entry": risk.get("risk_pct"),
            "corr_reduction": reduction,
        }
        risk_line = f" | risque social/news: {risk['risk_pct']}%" + (f" ({risk['top_headline'][:60]})" if risk.get("top_headline") else "")
        corr_line = f" | taille reduite de {reduction:.0%} (correlee a une position ouverte)" if reduction else ""
        _safe_send(send_fn, f"[ALPACA-STOCKS] BUY {symbol} qty={qty} ~${entry_price:.2f} (${cost:.0f}, score={score}){corr_line}{risk_line} | cash restant ${ledger['virtual_cash']:.0f}")
        log_event("Executor", "actions_us", "BUY", symbol, f"score {score} (13F + tendance)", score=score,
                  result="achete", price=entry_price, usd=round(cost, 2))
        return f"BUY(qty={qty})"

    elif action == "SELL" and has_position:
        if not market_open:
            return f"SELL(skip:market_closed,score={score})"
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
        cost = pos.get("cost") or round(pos["qty"] * (pos.get("entry_price") or 0), 2)
        ledger["virtual_cash"] = round(float(ledger.get("virtual_cash", VIRTUAL_STARTING_CASH)) + cost + (pnl or 0), 2)
        _record_trade_outcome(ledger, pnl)
        _safe_send(send_fn, f"[ALPACA-STOCKS] SELL {symbol} qty={pos['qty']} pnl=${pnl} (score={score}) | cash ${ledger['virtual_cash']:.0f}")
        log_event("Executor", "actions_us", "SELL", symbol, f"score {score}", score=score, result=pnl)
        return f"SELL(pnl={pnl})"

    return f"{action}(score={score},has_position={has_position})"


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
    invested = round(sum(p.get("cost") or p.get("qty", 0) * p.get("entry_price", 0)
                         for p in ledger["positions"].values()), 2)
    virtual_cash = round(float(ledger.get("virtual_cash", VIRTUAL_STARTING_CASH)), 2)
    return {
        "account": account,
        "virtual_cash": virtual_cash,
        "invested": invested,
        "total": round(virtual_cash + invested, 2),
        "starting_cash": VIRTUAL_STARTING_CASH,
        "realized_pnl": round(sum(t.get("pnl") or 0 for t in ledger["closed_trades"]), 2),
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
