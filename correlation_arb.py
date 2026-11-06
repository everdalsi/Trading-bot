"""Correlation analyst + arbitrage hunter (desk spec 06/11). Public data, stdlib only, fail-open, measurement mode.

CORRELATION
  matrix(symbols, hours=168) -> pairwise correlation of hourly returns (Binance spot), plus
  concentration(positions)   -> the book's effective number of independent bets: 20 shorts that all move like BTC
                                are ~1 bet, not 20 (exposure controller input)
  lead_lag(leader, follower) -> best lag (1-5 min) and correlation of 1-min returns: does BTC lead this alt? A
                                stable lead gives the micro desk a few seconds/minutes of edge after a BTC move.
ARBITRAGE
  spreads(coins)             -> best bid/ask across Binance spot, Binance futures, Bybit linear, OKX swap, Hyperliquid
                                mid: gross spread and net of 2 taker fees; flags spreads > costs (rare, short-lived)
"""
import json
import math
import time
import urllib.request

UA = {"User-Agent": "trading-bot-paper/1.0"}
TAKER = 0.0005           # conservative taker fee per side for the net spread


def _get(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=12) as r:
        return json.loads(r.read())


def _rets(symbol: str, interval: str, limit: int) -> list[float]:
    k = _get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit + 1}")[:-1]
    c = [float(x[4]) for x in k]
    return [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]


def _corr(a: list[float], b: list[float]) -> float | None:
    n = min(len(a), len(b))
    if n < 10:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb) if va and vb else None


def matrix(symbols: list[str], hours: int = 168) -> dict:
    rets = {}
    for s in symbols:
        try:
            rets[s] = _rets(s, "1h", hours)
        except Exception:
            continue
    out = {}
    keys = list(rets)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            c = _corr(rets[a], rets[b])
            if c is not None:
                out[f"{a}|{b}"] = round(c, 3)
    return out


def concentration(positions: list[dict], hours: int = 168) -> dict:
    """positions [{symbol, side, usd}] -> effective number of independent bets (1 / sum of squared risk shares
    after accounting for correlations), and the share of the book that moves with BTC."""
    syms = sorted({p["symbol"] for p in positions} | {"BTCUSDT"})
    rets = {}
    for s in syms:
        try:
            rets[s] = _rets(s, "1h", hours)
        except Exception:
            pass
    pos = [p for p in positions if p["symbol"] in rets]
    if not pos:
        return {"n_bets": None}
    w = [p.get("usd", 0) * (1 if p["side"].upper() == "LONG" else -1) for p in pos]
    tot = sum(abs(x) for x in w) or 1
    var = 0.0
    for i, pi in enumerate(pos):
        for j, pj in enumerate(pos):
            c = 1.0 if i == j else (_corr(rets[pi["symbol"]], rets[pj["symbol"]]) or 0.0)
            var += (w[i] / tot) * (w[j] / tot) * c
    gross = sum((abs(x) / tot) ** 2 for x in w)
    btc_beta = sum((w[i] / tot) * (_corr(rets[p["symbol"]], rets["BTCUSDT"]) or 0.0) for i, p in enumerate(pos))
    return {"n_positions": len(pos), "n_bets_naive": round(1 / gross, 1) if gross else None,
            "portfolio_var_ratio": round(var / gross, 2) if gross else None,
            "n_bets_effective": round(1 / max(var, 1e-9), 1), "net_btc_correlation": round(btc_beta, 2)}


def lead_lag(leader: str = "BTCUSDT", follower: str = "SOLUSDT", minutes: int = 600) -> dict:
    a, b = _rets(leader, "1m", minutes), _rets(follower, "1m", minutes)
    best = {"lag_min": 0, "corr": round(_corr(a, b) or 0, 3)}
    for lag in range(1, 6):
        c = _corr(a[:-lag], b[lag:])
        if c is not None and c > best["corr"] + 0.02:
            best = {"lag_min": lag, "corr": round(c, 3)}
    best["same_minute_corr"] = round(_corr(a, b) or 0, 3)
    return {"leader": leader, "follower": follower, **best}


def _quotes(coin: str) -> dict[str, tuple[float, float]]:
    q = {}
    try:
        t = _get(f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={coin}USDT")
        q["binance_spot"] = (float(t["bidPrice"]), float(t["askPrice"]))
    except Exception:
        pass
    try:
        t = _get(f"https://fapi.binance.com/fapi/v1/ticker/bookTicker?symbol={coin}USDT")
        q["binance_perp"] = (float(t["bidPrice"]), float(t["askPrice"]))
    except Exception:
        pass
    try:
        t = _get(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={coin}USDT")["result"]["list"][0]
        q["bybit_perp"] = (float(t["bid1Price"]), float(t["ask1Price"]))
    except Exception:
        pass
    try:
        t = _get(f"https://www.okx.com/api/v5/market/ticker?instId={coin}-USDT-SWAP")["data"][0]
        q["okx_perp"] = (float(t["bidPx"]), float(t["askPx"]))
    except Exception:
        pass
    return q


def spreads(coins: list[str]) -> list[dict]:
    out = []
    for coin in coins:
        q = _quotes(coin)
        if len(q) < 2:
            continue
        buy_v, (_, ask) = min(q.items(), key=lambda kv: kv[1][1])           # cheapest ask
        sell_v, (bid, _) = max(q.items(), key=lambda kv: kv[1][0])          # highest bid
        gross = bid / ask - 1
        net = gross - 2 * TAKER
        out.append({"coin": coin, "buy_on": buy_v, "sell_on": sell_v, "gross_pct": round(100 * gross, 4),
                    "net_pct": round(100 * net, 4), "opportunity": net > 0 and buy_v != sell_v, "venues": len(q)})
    return sorted(out, key=lambda r: -r["net_pct"])


if __name__ == "__main__":
    t = time.time()
    print(matrix(["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]))
    print(concentration([{"symbol": s, "side": "SHORT", "usd": 30} for s in ("SOLUSDT", "XRPUSDT", "ETHUSDT", "ADAUSDT", "DOGEUSDT")]
                        + [{"symbol": "BTCUSDT", "side": "LONG", "usd": 400}]))
    print(lead_lag("BTCUSDT", "SOLUSDT"))
    for r in spreads(["BTC", "ETH", "SOL", "XRP", "DOGE", "HYPE"]):
        print(r)
    print(f"{time.time() - t:.1f} s")
