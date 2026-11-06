"""Backtester + quant researcher (desk spec 06/11: "teste chaque strategie sur l'historique avant utilisation" /
"invente et ameliore les strategies"). Stdlib only, Binance public 1-min klines, measurement: nothing is applied.

Strategies (same logic as scalper.py, parametrised):
  breakout  close breaks the high/low of the previous `look` minutes with volume >= `vmult` x average  (scalper A)
  meanrev   RSI(14) < `rsi_lo` (or > 100-rsi_lo) and close beyond `bands` sigma of the 20-bar mean      (scalper B)
Exits: take profit `tp`, stop loss `sl`, forced exit after `hold` minutes. Costs per side = fee + slippage (taker),
applied at entry and exit: the result is NET, like the real desks.

Walk-forward: the days are cut in folds; for each fold the researcher picks the best parameters on the previous
days (train) and measures them on the fold (test). Only out-of-sample (test) numbers are reported. A parameter set
is "robust" when it is net positive on most test folds with enough trades.

    python backtester.py BTCUSDT ETHUSDT SOLUSDT --days 7            -> report + DATA_DIR/backtest_report.json
"""
import itertools
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
COST_SIDE = float(os.environ.get("BT_COST_SIDE", 0.0012))      # 0.10 % taker + 0.02 % slippage (Binance spot)
GRID = {
    "breakout": {"look": [10, 15, 30], "vmult": [1.5, 2.0, 3.0], "tp": [0.003, 0.005, 0.008], "sl": [0.003, 0.005, 0.008], "hold": [3, 5]},
    "meanrev": {"rsi_lo": [15, 20, 25], "bands": [2.0, 2.5, 3.0], "tp": [0.003, 0.005, 0.008], "sl": [0.003, 0.005, 0.008], "hold": [3, 5]},
}


def klines(symbol: str, days: int) -> list[tuple]:
    """(open_time_ms, open, high, low, close, volume) 1-min bars, oldest first, closed bars only."""
    end = int(time.time() // 60 * 60_000)
    start = end - days * 86_400_000
    out, t = [], start
    while t < end:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&startTime={t}&limit=1000"
        with urllib.request.urlopen(url, timeout=20) as r:
            rows = json.loads(r.read())
        if not rows:
            break
        out += [(k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])) for k in rows]
        t = rows[-1][0] + 60_000
        time.sleep(0.15)                                           # stay far below the public rate limit
    return [b for b in out if b[0] < end]


def _rsi(c: list[float], i: int, n: int = 14) -> float:
    gains = losses = 0.0
    for j in range(i - n + 1, i + 1):
        d = c[j] - c[j - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    return 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)


def signals(bars: list[tuple], strat: str, p: dict) -> list[tuple[int, str]]:
    c = [b[4] for b in bars]
    out = []
    if strat == "breakout":
        L = p["look"]
        for i in range(L + 1, len(bars)):
            prev = bars[i - L:i]
            hi, lo = max(x[2] for x in prev), min(x[3] for x in prev)
            va = sum(x[5] for x in prev) / L
            if va > 0 and bars[i][5] >= p["vmult"] * va:
                if c[i] > hi:
                    out.append((i, "LONG"))
                elif c[i] < lo:
                    out.append((i, "SHORT"))
    else:
        for i in range(25, len(bars)):
            r = _rsi(c, i)
            w = c[i - 19:i + 1]
            m = sum(w) / 20
            sd = (sum((x - m) ** 2 for x in w) / 20) ** 0.5
            if r < p["rsi_lo"] and c[i] < m - p["bands"] * sd:
                out.append((i, "LONG"))
            elif r > 100 - p["rsi_lo"] and c[i] > m + p["bands"] * sd:
                out.append((i, "SHORT"))
    return out


def simulate(bars: list[tuple], strat: str, p: dict) -> list[float]:
    """Net return of each trade (one position at a time per symbol)."""
    res, busy_until = [], -1
    for i, side in signals(bars, strat, p):
        if i <= busy_until or i + 1 >= len(bars):
            continue
        entry = bars[i][4]
        sign = 1 if side == "LONG" else -1
        exit_px, j = None, i
        for j in range(i + 1, min(len(bars), i + 1 + p["hold"])):
            hi, lo = bars[j][2], bars[j][3]
            fav, adv = (hi / entry - 1, lo / entry - 1) if sign > 0 else (1 - lo / entry, 1 - hi / entry)
            if adv <= -p["sl"]:                                     # stop first (pessimistic when both touch)
                exit_px = entry * (1 - sign * p["sl"])
                break
            if fav >= p["tp"]:
                exit_px = entry * (1 + sign * p["tp"])
                break
        if exit_px is None:
            exit_px = bars[j][4]
        res.append(sign * (exit_px / entry - 1) - 2 * COST_SIDE)
        busy_until = j
    return res


def _stats(r: list[float]) -> dict:
    n = len(r)
    return {"trades": n, "net_avg_pct": round(100 * sum(r) / n, 4) if n else None,
            "win_rate": round(100 * sum(x > 0 for x in r) / n, 1) if n else None,
            "net_total_pct": round(100 * sum(r), 3)}


def walk_forward(data: dict[str, list[tuple]], strat: str, folds: int = 4, min_trades: int = 20) -> dict:
    keys, vals = zip(*GRID[strat].items())
    combos = [dict(zip(keys, v)) for v in itertools.product(*vals)]
    length = min(len(b) for b in data.values())
    edges = [int(length * k / (folds + 1)) for k in range(folds + 2)]
    tests = []
    for f in range(1, folds + 1):
        train = {s: b[:edges[f]] for s, b in data.items()}
        test = {s: b[edges[f]:edges[f + 1]] for s, b in data.items()}
        best, best_score = None, -1e9
        for p in combos:
            r = [x for b in train.values() for x in simulate(b, strat, p)]
            if len(r) >= min_trades:
                score = sum(r) / len(r)
                if score > best_score:
                    best, best_score = p, score
        if best is None:
            continue
        r_test = [x for b in test.values() for x in simulate(b, strat, best)]
        tests.append({"fold": f, "params": best, "train_net_avg_pct": round(100 * best_score, 4), "test": _stats(r_test)})
    pos = sum(1 for t in tests if (t["test"]["net_avg_pct"] or 0) > 0 and t["test"]["trades"] >= 5)
    all_test = [t["test"] for t in tests]
    n = sum(t["trades"] for t in all_test)
    avg = sum((t["net_avg_pct"] or 0) * t["trades"] for t in all_test) / n if n else None
    return {"strategy": strat, "folds": tests, "out_of_sample_trades": n,
            "out_of_sample_net_avg_pct": round(avg, 4) if avg is not None else None,
            "positive_folds": f"{pos}/{len(tests)}", "robust": bool(tests) and pos >= max(2, int(0.75 * len(tests)))}


def run(symbols: list[str], days: int = 7) -> dict:
    data = {s: klines(s, days) for s in symbols}
    current = {"breakout": {"look": 15, "vmult": 2.0, "tp": 0.005, "sl": 0.003, "hold": 5},
               "meanrev": {"rsi_lo": 20, "bands": 2.5, "tp": 0.005, "sl": 0.003, "hold": 5}}
    report = {"at": time.time(), "days": days, "symbols": symbols, "cost_per_side": COST_SIDE,
              "current_params": {k: _stats([x for b in data.values() for x in simulate(b, k, p)]) for k, p in current.items()},
              "walk_forward": {k: walk_forward(data, k) for k in GRID}, "mode": "mesure (rien n'est applique)"}
    try:
        (DATA_DIR / "backtest_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    except Exception:
        pass
    return report


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 7
    if "--days" in sys.argv:
        args = [a for a in args if a != str(days)]
    rep = run(args or ["BTCUSDT", "ETHUSDT", "SOLUSDT"], days)
    print("current params (all days):", json.dumps(rep["current_params"]))
    for k, w in rep["walk_forward"].items():
        print(k, "out-of-sample:", w["out_of_sample_trades"], "trades, net avg", w["out_of_sample_net_avg_pct"], "%,",
              "positive folds", w["positive_folds"], "robust" if w["robust"] else "NOT robust")
        for f in w["folds"]:
            print("   fold", f["fold"], f["params"], "test", f["test"])
