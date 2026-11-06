"""Manipulation detector (control with veto, desk spec 06/11). Binance spot public data, stdlib only, fail-open.

Flags a coin whose recent activity looks manufactured, so the desks do not buy into a pump or trade fake volume:
  pump        1-h return far outside the coin's own hourly volatility (z-score on 30 days of hourly candles)
  vol_spike   last hour's volume vs the median hourly volume of the past 7 days
  wash        share of the last 1000 trades sharing the SAME size (bots trading with themselves repeat sizes)
              + ping-pong: consecutive opposite trades of identical size
  thin_book   depth within +-2 % of the price vs the last hour's traded volume (easy to push)
score 0-1 = weighted flags; verdict "suspect" (>= 0.6), "watch" (>= 0.35), "ok". Measurement first: the bot logs the
verdict next to entries (IC / outcome audit) before it becomes a veto for memecoins and small caps.

    check(symbol) -> {score, verdict, flags{...}, raw{...}} | {"verdict": "unknown"}
"""
import json
import math
import time
import urllib.request
from collections import Counter

BINANCE = "https://api.binance.com"
_cache: dict[str, tuple[float, dict]] = {}


def _get(path: str, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    with urllib.request.urlopen(f"{BINANCE}{path}?{q}", timeout=12) as r:
        return json.loads(r.read())


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def check(symbol: str, ttl: float = 60) -> dict:
    hit = _cache.get(symbol)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        kl = _get("/api/v3/klines", symbol=symbol, interval="1h", limit=721)[:-1]       # closed hours, 30 days
        closes = [float(k[4]) for k in kl]
        vols = [float(k[7]) for k in kl]                                                # quote volume
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        mu = sum(rets[:-1]) / (len(rets) - 1)
        sd = (sum((r - mu) ** 2 for r in rets[:-1]) / (len(rets) - 2)) ** 0.5 or 1e-9
        z = (rets[-1] - mu) / sd
        med7 = sorted(vols[-169:-1])[84] or 1e-9
        vol_ratio = vols[-1] / med7

        trades = _get("/api/v3/aggTrades", symbol=symbol, limit=1000)
        sizes = [t["q"] for t in trades]
        top_share = Counter(sizes).most_common(1)[0][1] / len(sizes) if sizes else 0.0
        ping = sum(1 for a, b in zip(trades, trades[1:]) if a["q"] == b["q"] and a["m"] != b["m"])
        ping_share = ping / max(1, len(trades) - 1)

        book = _get("/api/v3/depth", symbol=symbol, limit=500)
        px = closes[-1]
        depth = sum(float(p) * float(q) for p, q in book["bids"] if float(p) >= px * 0.98) + \
            sum(float(p) * float(q) for p, q in book["asks"] if float(p) <= px * 1.02)
        thin = vols[-1] / depth if depth else 99.0                                      # hour volume / 2 % depth

        flags = {"pump": _clip01((abs(z) - 3) / 4), "vol_spike": _clip01((vol_ratio - 3) / 7),
                 "wash": _clip01(max((top_share - 0.08) / 0.3, (ping_share - 0.05) / 0.25)),
                 "thin_book": _clip01((thin - 5) / 20)}
        w = {"pump": 0.3, "vol_spike": 0.2, "wash": 0.3, "thin_book": 0.2}
        score = round(sum(w[k] * v for k, v in flags.items()), 3)
        # a pump on a thin book is the classic pump-and-dump shape: count it fully
        if flags["pump"] > 0.5 and flags["thin_book"] > 0.5:
            score = max(score, 0.7)
        out = {"score": score, "verdict": "suspect" if score >= 0.6 else "watch" if score >= 0.35 else "ok",
               "flags": {k: round(v, 2) for k, v in flags.items()},
               "raw": {"ret_1h_z": round(z, 2), "vol_ratio_7d": round(vol_ratio, 2), "same_size_share": round(top_share, 3),
                       "ping_pong_share": round(ping_share, 3), "hour_vol_over_2pct_depth": round(thin, 2)}}
    except Exception as e:
        out = {"verdict": "unknown", "error": type(e).__name__}
    _cache[symbol] = (time.time(), out)
    return out


if __name__ == "__main__":
    import sys
    for s in sys.argv[1:] or ["BTCUSDT", "SOLUSDT", "PENGUUSDT", "WLFIUSDT", "TRUMPUSDT"]:
        print(s, json.dumps(check(s)))
