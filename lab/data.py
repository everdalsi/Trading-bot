"""Public, key-free market data for the lab, with a small in-memory TTL cache. Every function returns None/{}/[]
on any failure (fail-closed: a slot without data does nothing). No private endpoint, no key, no order.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import date

UA = {"User-Agent": "Mozilla/5.0 (paper-trading-lab)"}
FAIL_TTL = 300  # a failed source is not retried for 5 min (no hammering a blocked or down API)
_cache: dict[str, tuple[float, object]] = {}
_failed: dict[str, float] = {}


def _get(url: str, ttl: float, body: dict | None = None):
    key = url + (json.dumps(body, sort_keys=True) if body else "")
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    if time.time() - _failed.get(key, 0) < FAIL_TTL:
        return hit[1] if hit else None  # stale value beats nothing while the source is down
    try:
        data = json.dumps(body).encode() if body is not None else None
        headers = {**UA, "Content-Type": "application/json"} if body is not None else UA
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=15) as r:
            out = json.loads(r.read())
    except Exception:
        _failed[key] = time.time()
        return hit[1] if hit else None
    _cache[key] = (time.time(), out)
    return out


def spot_prices(symbols) -> dict:
    """{'BTCUSDT': 91234.5, ...} from Binance spot; unknown symbols are simply absent."""
    wanted = set(symbols)
    if not wanted:
        return {}
    rows = _get("https://api.binance.com/api/v3/ticker/price", ttl=30)
    if not isinstance(rows, list):
        return {}
    out = {}
    for r in rows:
        try:
            if r.get("symbol") in wanted and float(r["price"]) > 0:
                out[r["symbol"]] = float(r["price"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def klines(symbol: str, interval: str, limit: int, ttl: float = 3600) -> list[dict]:
    """Binance spot candles, oldest first: [{t, high, low, close}]."""
    rows = _get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}", ttl)
    try:
        return [{"t": r[0], "high": float(r[2]), "low": float(r[3]), "close": float(r[4])} for r in rows]
    except (TypeError, IndexError, ValueError):
        return []


def hl_perps(ttl: float = 60) -> list[dict]:
    """Hyperliquid perps: [{coin, funding_h, oi_usd, mark}]; funding_h is the hourly rate (HL pays every hour)."""
    out = _get("https://api.hyperliquid.xyz/info", ttl, body={"type": "metaAndAssetCtxs"})
    try:
        rows = []
        for meta, ctx in zip(out[0]["universe"], out[1]):
            mark = float(ctx.get("markPx") or 0)
            if mark > 0 and not meta.get("isDelisted"):
                rows.append({"coin": meta["name"], "funding_h": float(ctx.get("funding") or 0),
                             "oi_usd": float(ctx.get("openInterest") or 0) * mark, "mark": mark})
        return rows
    except (TypeError, KeyError, IndexError, ValueError):
        return []


def delivery_futures(ttl: float = 60) -> list[dict]:
    """Binance COIN-M quarterly futures: [{symbol, pair, price, expiry}] (perpetuals excluded)."""
    rows = _get("https://dapi.binance.com/dapi/v1/ticker/price", ttl)
    out = []
    for r in rows if isinstance(rows, list) else []:
        sym = r.get("symbol", "")
        if "_" not in sym or sym.endswith("_PERP"):
            continue
        try:
            yymmdd = sym.split("_")[1]
            expiry = date(2000 + int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6]))
            out.append({"symbol": sym, "pair": r.get("ps") or sym.split("_")[0], "price": float(r["price"]),
                        "expiry": expiry.isoformat()})
        except (ValueError, KeyError, IndexError, TypeError):
            continue
    return out


def yahoo_daily(symbol: str, ttl: float = 3600) -> list[tuple[str, float]]:
    """[(YYYY-MM-DD, close)] for the last year, oldest first."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?range=1y&interval=1d")
    out = _get(url, ttl)
    try:
        res = out["chart"]["result"][0]
        closes = res["indicators"]["quote"][0]["close"]
        return [(time.strftime("%Y-%m-%d", time.gmtime(ts)), float(c))
                for ts, c in zip(res["timestamp"], closes) if c is not None]
    except (TypeError, KeyError, IndexError, ValueError):
        return []
