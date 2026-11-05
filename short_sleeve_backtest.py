"""Replay of lane A (momentum alts) of short_sleeve.py on Binance daily candles, spec §4.

    uv run --with requests python short_sleeve_backtest.py [days]

Honest limits, printed with the result:
- universe = TODAY's top-50 by volume (survivorship bias: coins that died are missing, which
  flatters the result); a fair test would need the historical top-50 of each day;
- the trailing stop is checked on the daily low (intraday path unknown), filled at the stop price;
- entries at the next day's open, costs 0.30 % per side (fee 0.10 % + slippage 0.20 %).
"""
import sys
import time

import requests

import short_sleeve as s


def klines(sym: str, days: int) -> list[tuple[int, float, float, float, float]]:
    out, end = [], None
    while len(out) < days:
        params = {"symbol": sym, "interval": "1d", "limit": 1000}
        if end:
            params["endTime"] = end
        rows = requests.get(f"{s.BINANCE}/api/v3/klines", params=params, timeout=20).json()
        if not rows:
            break
        out = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows] + out
        end = rows[0][0] - 1
        if len(rows) < 1000:
            break
        time.sleep(0.2)
    return out[-days:]


def run(days: int = 1100) -> None:
    syms = [x for x in s.universe() if x != "BTCUSDT"]
    data = {"BTCUSDT": klines("BTCUSDT", days)}
    for x in syms:
        data[x] = klines(x, days)
    dates = [k[0] for k in data["BTCUSDT"]]
    idx = {x: {k[0]: k for k in data[x]} for x in data}
    first = {x: data[x][0][0] for x in data}

    cash, pos, trades, curve = s.STARTING_CASH, {}, [], []
    cost = s.A_COST
    for i in range(60, len(dates) - 1):
        d, nxt = dates[i], dates[i + 1]
        # trailing stops on day d
        for x in list(pos):
            k = idx[x].get(d)
            if not k:
                continue
            p = pos[x]
            stop = p["peak"] * (1 - s.A_TRAIL)
            if k[3] <= stop:
                px = min(stop, k[1])  # gap down: open below the stop
                pnl = p["qty"] * px * (1 - cost) - p["usd"]
                cash += p["usd"] + pnl; trades.append((pnl, "trail", d)); del pos[x]
            else:
                p["peak"] = max(p["peak"], k[2])
        # rotation at the close of day d, executed at the open of d+1
        btc = [idx["BTCUSDT"][t][4] for t in dates[i - 50:i + 1]]
        btc_r = btc[-1] / idx["BTCUSDT"][dates[i - s.A_LOOKBACK]][4] - 1
        btc_ok = btc[-1] > sum(btc[-50:]) / 50
        r14 = {}
        for x in syms:
            if d - first[x] < s.A_MIN_AGE_DAYS * 86_400_000:
                continue
            a, b = idx[x].get(dates[i - s.A_LOOKBACK]), idx[x].get(d)
            if a and b:
                r14[x] = b[4] / a[4] - 1
        top = sorted((x for x, r in r14.items() if r > 0 and r > btc_r), key=lambda x: r14[x], reverse=True)[:s.A_MAX_POS]
        for x in list(pos):
            if x not in top or r14.get(x, -1) < 0:
                k = idx[x].get(nxt)
                if k:
                    pnl = pos[x]["qty"] * k[1] * (1 - cost) - pos[x]["usd"]
                    cash += pos[x]["usd"] + pnl; trades.append((pnl, "rotation", nxt)); del pos[x]
        eq = cash + sum(p["qty"] * idx[x][d][4] for x, p in pos.items() if d in idx[x])
        if btc_ok:
            for x in top:
                k = idx[x].get(nxt)
                if x in pos or not k or len(pos) >= s.A_MAX_POS:
                    continue
                usd = min(eq * s.A_POS_PCT, cash)
                if usd < 5:
                    continue
                cash -= usd
                pos[x] = {"qty": usd / (k[1] * (1 + cost)), "usd": usd, "peak": k[1]}
        curve.append((d, eq))

    gains = sum(p for p, _, _ in trades if p > 0)
    losses = -sum(p for p, _, _ in trades if p < 0)
    peak, mdd = 0.0, 0.0
    for _, e in curve:
        peak = max(peak, e); mdd = max(mdd, 1 - e / peak)
    years = (curve[-1][0] - curve[0][0]) / 3.15e10
    final = curve[-1][1]
    print(f"Lane A replay: {len(trades)} trades over {years:.1f} years, universe {len(syms)} coins")
    print(f"  equity ${s.STARTING_CASH:,.0f} -> ${final:,.2f} ({(final / s.STARTING_CASH - 1) * 100:+.1f} %)")
    print(f"  profit factor {gains / losses if losses else float('inf'):.2f}, win rate "
          f"{sum(p > 0 for p, _, _ in trades) / max(1, len(trades)) * 100:.0f} %, max drawdown {mdd * 100:.1f} %")
    # walk-forward view: 6 equal windows
    n = len(curve) // 6
    wins = []
    for w in range(6):
        a, b = curve[w * n][1], curve[min(len(curve) - 1, (w + 1) * n)][1]
        wins.append(b / a - 1)
    print("  6 windows:", " ".join(f"{x * 100:+.1f}%" for x in wins), f"-> {sum(x > 0 for x in wins)}/6 positive")
    print("  spec targets: >= 300 trades, PF >= 1.3, max DD <= 20 %, >= 4/6 windows positive")
    print("  LIMITS: today's top-50 (survivorship bias, flatters the result); trailing stop on daily lows.")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 1100)
