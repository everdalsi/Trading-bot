"""
congress_trades_signal.py -- Standalone, isolated Congress-trading-disclosure
conviction signal (STOCK Act Periodic Transaction Reports).

STATUS: NOT wired into bot.py or the live trading loop. Zero imports from
bot.py/agents/* on purpose (same isolation contract as strategy_ledger.py) --
importing this module never touches sim state, never opens a socket at
import time, and can never trigger any bot.py module-level side effect.

WHAT THIS IS (and isn't): legally public disclosures filed by US House and
Senate members under the STOCK Act, required within 45 days of a covered
transaction. This is NOT a real-time signal and never will be -- treat any
ticker surfaced here as a multi-week-old-at-best conviction hint, never a
trade trigger. Real observed lag (see conviction()'s per-row `lag_days` and
the summary's `median_lag_days`/`max_lag_days`) is sometimes close to the
45-day legal minimum but documented cases (2025-2026 House ethics reporting)
show plenty of disclosures filed months to nearly two years late with
essentially no enforcement (the standard $200 late fee is routinely waived,
zero criminal prosecutions on record) -- never assume "not yet in the feed"
means "no trade happened".

SOURCES (researched + verified live 2026-09-08, see project memory for the
full research trail):

  HOUSE -- github.com/TattooedHead/house-stock-watcher-data, an actively
  maintained (pushed the same day this was verified) open-source pipeline
  that parses official disclosures-clerk.house.gov PTR PDFs into clean JSON.
  Verified live: 24,024 records, most recent transaction_date 2026-08-26
  disclosed 2026-09-03 (8 days -- that's the fast end of the range, not
  typical; don't assume every row is that fresh). Free, no auth, served as
  a plain JSON file off raw.githubusercontent.com.

  SENATE -- no working free source found. The one previously-known
  aggregator, github.com/timothycarambat/senate-stock-watcher-data, is DEAD:
  verified live, its data stops at 2020-12-02 with nothing scraped/filed
  since (repo has ~12 commits total, effectively abandoned). Senate's own
  efdsearch.senate.gov is technically public/no-login but requires accepting
  a search-terms session and returns PDFs, not JSON -- would need a
  dedicated scraper, deliberately not built here. QuiverQuant covers Senate
  too but has NO free tier ($30-75/mo as of 2026) -- excluded per the
  explicit "no paid third-party accounts" constraint this module was built
  under. get_senate_transactions() below is a deliberate NotImplementedError
  stub, not a silent empty return, so a caller can never mistake "no Senate
  data available" for "no Senate trades happened this period".

USAGE:
    from congress_trades_signal import fetch_house_transactions, conviction
    txs = fetch_house_transactions()             # cached, refreshes >=6h
    signal = conviction(txs, lookback_days=30)
    for row in signal["by_ticker"][:10]:
        print(row)
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, date
from typing import Optional

HOUSE_DATA_URL = (
    "https://raw.githubusercontent.com/TattooedHead/"
    "house-stock-watcher-data/main/data/all_transactions.json"
)
DEFAULT_CACHE_PATH = os.path.join(os.path.dirname(__file__), ".cache_house_trades.json")
DEFAULT_CACHE_MAX_AGE_SEC = 6 * 3600  # the source repo updates roughly daily; no need to refetch 11MB every call
REQUEST_TIMEOUT_SEC = 20


def get_senate_transactions(*_args, **_kwargs):
    """Deliberate stub -- see module docstring. Raises rather than returning
    [] so callers can't silently treat "no free Senate source" as "no Senate
    trading happened". Replace this only after wiring a real, actively
    maintained free source (or an in-house efdsearch.senate.gov scraper)."""
    raise NotImplementedError(
        "No working free Senate trading-disclosure source as of 2026-09-08 "
        "research (senate-stock-watcher-data is dead since 2020-12-02; "
        "QuiverQuant has no free tier; efdsearch.senate.gov needs a "
        "dedicated PDF scraper, not built). House-side data is available "
        "via fetch_house_transactions()."
    )


def _fetch_json_url(url: str, timeout: float = REQUEST_TIMEOUT_SEC):
    req = urllib.request.Request(url, headers={"User-Agent": "congress-trades-signal-research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_house_transactions(
    cache_path: str = DEFAULT_CACHE_PATH,
    max_age_sec: int = DEFAULT_CACHE_MAX_AGE_SEC,
    force_refresh: bool = False,
) -> list[dict]:
    """Returns the raw list of House PTR transaction dicts, disk-cached
    (atomic write, same os.replace pattern as strategy_ledger.py) so repeated
    calls in one process/day don't re-pull an ~11MB file each time."""
    if not force_refresh and os.path.exists(cache_path):
        age = time.time() - os.path.getmtime(cache_path)
        if age < max_age_sec:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)

    data = _fetch_json_url(HOUSE_DATA_URL)
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, cache_path)
    return data


def _parse_mmddyyyy(s: Optional[str]) -> Optional[date]:
    if not s or s == "--":
        return None
    try:
        return datetime.strptime(s.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def conviction(
    transactions: list[dict],
    lookback_days: int = 30,
    as_of: Optional[date] = None,
    min_amount_mid: float = 0.0,
) -> dict:
    """Aggregate raw House PTR rows into a per-ticker conviction summary over
    the trailing `lookback_days`, windowed on disclosure_date (the date the
    trade became *knowable*, not transaction_date -- using transaction_date
    would understate real-world staleness).

    Returns {"as_of": iso date, "lookback_days": int, "by_ticker": [rows...]}
    where each row is:
        {ticker, buys, sells, net_buyers, unique_filers, total_amount_mid,
         median_lag_days, max_lag_days, tickers_asset_desc}
    sorted by (buys - sells) desc, then total_amount_mid desc -- a simple,
    transparent conviction ordering (net buy pressure, tie-broken by dollar
    size), not a black-box score.
    """
    as_of = as_of or date.today()
    window_start = as_of - timedelta(days=lookback_days)

    by_ticker: dict[str, dict] = {}
    for row in transactions:
        ticker = (row.get("ticker") or "").strip().upper()
        if not ticker or ticker in ("--", "N/A", ""):
            continue
        disc = _parse_mmddyyyy(row.get("disclosure_date"))
        if disc is None or not (window_start <= disc <= as_of):
            continue
        txn = _parse_mmddyyyy(row.get("transaction_date"))
        amount_mid = row.get("amount_mid") or 0
        try:
            amount_mid = float(amount_mid)
        except (TypeError, ValueError):
            amount_mid = 0.0
        if amount_mid < min_amount_mid:
            continue

        entry = by_ticker.setdefault(ticker, {
            "ticker": ticker,
            "buys": 0, "sells": 0,
            "filers": set(),
            "total_amount_mid": 0.0,
            "lags": [],
            "asset_desc": row.get("asset_description", ""),
        })
        ttype = (row.get("type") or "").lower()
        if "purchase" in ttype:
            entry["buys"] += 1
        elif "sale" in ttype:
            entry["sells"] += 1
        filer = row.get("representative") or row.get("senator") or "unknown"
        entry["filers"].add(filer)
        entry["total_amount_mid"] += amount_mid
        if disc and txn:
            entry["lags"].append((disc - txn).days)

    rows = []
    for ticker, e in by_ticker.items():
        lags = sorted(e["lags"])
        median_lag = lags[len(lags) // 2] if lags else None
        rows.append({
            "ticker": ticker,
            "asset_desc": e["asset_desc"],
            "buys": e["buys"],
            "sells": e["sells"],
            "net_buy_pressure": e["buys"] - e["sells"],
            "unique_filers": len(e["filers"]),
            "total_amount_mid": round(e["total_amount_mid"], 2),
            "median_lag_days": median_lag,
            "max_lag_days": max(lags) if lags else None,
        })
    rows.sort(key=lambda r: (r["net_buy_pressure"], r["total_amount_mid"]), reverse=True)

    return {"as_of": as_of.isoformat(), "lookback_days": lookback_days, "by_ticker": rows}


# ---------------------------------------------------------------------- #
# Unit tests (stdlib only, NO network calls -- run with:
#   python congress_trades_signal.py
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    import unittest

    FIXTURE_AS_OF = date(2026, 9, 8)

    def _row(ticker, ttype, disc, txn, amount_mid=8000, rep="Rep A"):
        return {
            "ticker": ticker, "type": ttype,
            "disclosure_date": disc, "transaction_date": txn,
            "amount_mid": amount_mid, "representative": rep,
            "asset_description": f"{ticker} Inc.",
        }

    class TestCongressTradesSignal(unittest.TestCase):
        def test_senate_stub_raises(self):
            with self.assertRaises(NotImplementedError):
                get_senate_transactions()

        def test_date_parsing(self):
            self.assertEqual(_parse_mmddyyyy("08/26/2026"), date(2026, 8, 26))
            self.assertIsNone(_parse_mmddyyyy("--"))
            self.assertIsNone(_parse_mmddyyyy(None))
            self.assertIsNone(_parse_mmddyyyy("not-a-date"))

        def test_basic_net_buy_pressure(self):
            txs = [
                _row("NVDA", "Purchase", "09/01/2026", "08/20/2026"),
                _row("NVDA", "Purchase", "09/02/2026", "08/22/2026", rep="Rep B"),
                _row("NVDA", "Sale (Full)", "09/03/2026", "08/25/2026"),
            ]
            sig = conviction(txs, lookback_days=30, as_of=FIXTURE_AS_OF)
            row = sig["by_ticker"][0]
            self.assertEqual(row["ticker"], "NVDA")
            self.assertEqual(row["buys"], 2)
            self.assertEqual(row["sells"], 1)
            self.assertEqual(row["net_buy_pressure"], 1)
            self.assertEqual(row["unique_filers"], 2)

        def test_lookback_window_excludes_old_rows(self):
            txs = [
                _row("OLD", "Purchase", "01/01/2026", "12/20/2025"),  # way outside 30d window from FIXTURE_AS_OF
                _row("NEW", "Purchase", "09/05/2026", "08/25/2026"),
            ]
            sig = conviction(txs, lookback_days=30, as_of=FIXTURE_AS_OF)
            tickers = [r["ticker"] for r in sig["by_ticker"]]
            self.assertIn("NEW", tickers)
            self.assertNotIn("OLD", tickers)

        def test_ticker_placeholder_excluded(self):
            txs = [_row("--", "Purchase", "09/01/2026", "08/20/2026")]
            sig = conviction(txs, lookback_days=30, as_of=FIXTURE_AS_OF)
            self.assertEqual(sig["by_ticker"], [])

        def test_lag_days_computed(self):
            txs = [_row("AAPL", "Purchase", "09/05/2026", "08/01/2026")]
            sig = conviction(txs, lookback_days=30, as_of=FIXTURE_AS_OF)
            row = sig["by_ticker"][0]
            self.assertEqual(row["median_lag_days"], 35)
            self.assertEqual(row["max_lag_days"], 35)

        def test_min_amount_filter(self):
            txs = [
                _row("TSLA", "Purchase", "09/01/2026", "08/20/2026", amount_mid=5000),
                _row("META", "Purchase", "09/01/2026", "08/20/2026", amount_mid=500000),
            ]
            sig = conviction(txs, lookback_days=30, as_of=FIXTURE_AS_OF, min_amount_mid=100000)
            tickers = [r["ticker"] for r in sig["by_ticker"]]
            self.assertEqual(tickers, ["META"])

        def test_sort_order_ties_broken_by_dollar_size(self):
            txs = [
                _row("SMALL", "Purchase", "09/01/2026", "08/20/2026", amount_mid=1000),
                _row("BIG", "Purchase", "09/01/2026", "08/20/2026", amount_mid=900000),
            ]
            sig = conviction(txs, lookback_days=30, as_of=FIXTURE_AS_OF)
            self.assertEqual(sig["by_ticker"][0]["ticker"], "BIG")

        def test_empty_input(self):
            sig = conviction([], lookback_days=30, as_of=FIXTURE_AS_OF)
            self.assertEqual(sig["by_ticker"], [])
            self.assertEqual(sig["as_of"], "2026-09-08")

    unittest.main(verbosity=2)
