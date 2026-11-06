"""Order-flow entry filter for the 1-5 min scalper (Desktop, improvement 2 of 06/11).

Why: the crypto scalper enters on price patterns only and is stopped out by noise (19 % winners, SL 0.30 %).
Research on BTC/USDT (Fee Structure and Order Flow Informativeness, 2025) finds trade-flow imbalance (TFI,
aggressive buys vs aggressive sells) explains a large share of 1-min price moves, more than book imbalance.
So an entry is allowed only when the recent tape agrees with the trade's side.

    entry_ok(symbol, side) -> (bool, details)   side "LONG"/"SHORT"; fail-open (True) on any data error
    stop_pct(symbol)       -> float | None      ATR-based stop (fraction), None on data error (keep the default SL)

Data: Binance spot public REST (aggTrades over the last WINDOW_S seconds, depth top 10). Cached CACHE_S per symbol.
Every evaluation is appended to DATA_DIR/orderflow_log.jsonl so the Professor / IC monitor can measure the
filter (would the blocked entries have lost?) before anyone tightens it. Thresholds overridable by env.
"""
import json
import os
import time
from pathlib import Path

import requests

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
BINANCE = "https://api.binance.com"
WINDOW_S = int(os.environ.get("OF_WINDOW_S", 60))
TFI_MIN = float(os.environ.get("OF_TFI_MIN", 0.15))      # LONG needs TFI >= +0.15, SHORT <= -0.15
OBI_AGAINST = float(os.environ.get("OF_OBI_AGAINST", 0.30))  # book leaning >= 0.30 against the side blocks
MIN_TRADES = int(os.environ.get("OF_MIN_TRADES", 20))     # thin tape -> no opinion (fail-open)
ATR_MULT = float(os.environ.get("OF_ATR_MULT", 1.5))
STOP_MIN, STOP_MAX = 0.0030, 0.0100
CACHE_S = 10
_cache: dict[tuple, tuple[float, object]] = {}


def _cached(key: tuple, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def _log(row: dict) -> None:
    try:
        with open(DATA_DIR / "orderflow_log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def flow(symbol: str) -> dict:
    """TFI over the last WINDOW_S seconds and top-10 book imbalance, both in [-1, +1]."""
    def fetch():
        # latest 1000 prints, then keep the window (startTime would return the OLDEST 1000 on a busy minute)
        since_ms = int(time.time() * 1000) - WINDOW_S * 1000
        trades = requests.get(f"{BINANCE}/api/v3/aggTrades", params={"symbol": symbol, "limit": 1000},
                              timeout=8).json()
        trades = [t for t in trades if t["T"] >= since_ms]
        buy = sum(float(t["q"]) * float(t["p"]) for t in trades if not t["m"])   # m = buyer is maker -> taker sold
        sell = sum(float(t["q"]) * float(t["p"]) for t in trades if t["m"])
        depth = requests.get(f"{BINANCE}/api/v3/depth", params={"symbol": symbol, "limit": 10}, timeout=8).json()
        bids = sum(float(p) * float(q) for p, q in depth["bids"])
        asks = sum(float(p) * float(q) for p, q in depth["asks"])
        return {"n_trades": len(trades), "buy_usd": round(buy, 2), "sell_usd": round(sell, 2),
                "tfi": round((buy - sell) / (buy + sell), 4) if buy + sell else 0.0,
                "obi": round((bids - asks) / (bids + asks), 4) if bids + asks else 0.0}
    return _cached(("flow", symbol), fetch)


def entry_ok(symbol: str, side: str) -> tuple[bool, dict]:
    try:
        f = dict(flow(symbol))
        sign = 1 if side.upper() == "LONG" else -1
        if f["n_trades"] < MIN_TRADES:
            ok, why = True, "tape trop mince, pas d'avis"
        elif sign * f["tfi"] < TFI_MIN:
            ok, why = False, f"flux contre ou neutre (TFI {f['tfi']:+.2f}, besoin {sign * TFI_MIN:+.2f})"
        elif sign * f["obi"] <= -OBI_AGAINST:
            ok, why = False, f"carnet penche contre (OBI {f['obi']:+.2f})"
        else:
            ok, why = True, f"flux d'accord (TFI {f['tfi']:+.2f}, OBI {f['obi']:+.2f})"
        f.update(ok=ok, why=why)
    except Exception as e:  # fail-open: never block the scalper on a data error
        f = {"ok": True, "why": f"erreur donnees: {type(e).__name__}"}
    _log({"ts": time.time(), "symbol": symbol, "side": side, **f})
    return f["ok"], f


def stop_pct(symbol: str) -> float | None:
    """ATR(14) of 1-min bars x ATR_MULT as a fraction of price, clamped to 0.30-1.00 %."""
    try:
        def fetch():
            k = requests.get(f"{BINANCE}/api/v3/klines", params={"symbol": symbol, "interval": "1m", "limit": 16},
                             timeout=8).json()
            h, l, c = [float(x[2]) for x in k], [float(x[3]) for x in k], [float(x[4]) for x in k]
            tr = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(k))]
            atr = sum(tr[-14:]) / min(14, len(tr))
            return atr / c[-1]
        rel = _cached(("atr", symbol), fetch)
        return round(min(STOP_MAX, max(STOP_MIN, ATR_MULT * rel)), 5)
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    for s in sys.argv[1:] or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        for side in ("LONG", "SHORT"):
            print(s, side, entry_ok(s, side))
        print(s, "stop", stop_pct(s))
