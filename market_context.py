"""Market context agents from free public APIs (Desktop, public-apis round 2, user 06/11). MEASUREMENT MODE.

No agent here trades or vetoes: values are written to DATA_DIR/market_context.json every 5 min, shown on the
dashboard and recorded next to decisions until their IC proves they predict (user rule: >= 100 decisions).
Every call goes through one cache (TTL per source, respects each provider's free limit) and is fail-open: a
dead provider gives {"error": ...} for that block, never an exception, and api_health() reports it.

Sources (all keyless, tested 06/11):
  liquidations(sym)  MarginPad /api/v1/liquidations/recent   USD liquidated per price bucket over 24 h (60 req/min)
  crowd()            MarginPad /api/v1/long-short            retail long % per coin (crowded side, contrarian input)
  whales()           CoinLobster /api/public/hyperliquid/whales + /whale-radar   big HL perp trades, unusual flow
  funding(asset)     OptimistFi /api/perps/assets/{asset}    funding across ~5-40 venues incl. Hyperliquid
  news()             Crypto Vision /api/news                  free aggregated crypto news (NewsAgent source #3)

    run() -> dict         one refresh of everything (writes market_context.json)
    start(every_s=300)    background thread
    read() -> dict        last file (no network), for other agents
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "market_context.json"
UA = {"User-Agent": "trading-bot-paper/1.0 (research, paper trading only)"}
MAJORS = ("BTC", "ETH", "SOL")
TTL = {"marginpad": 120, "coinlobster": 180, "optimistfi": 300, "cryptovision": 300, "binance": 30}
_cache: dict[str, tuple[float, object]] = {}
_health: dict[str, dict] = {}


def _get(provider: str, url: str, **params):
    """GET through the shared cache; records health per provider; raises on error (callers catch)."""
    key = url + json.dumps(params, sort_keys=True)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL.get(provider, 120):
        return hit[1]
    t0 = time.time()
    try:
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        with urllib.request.urlopen(urllib.request.Request(full, headers=UA), timeout=12) as r:   # stdlib only:
            data = json.loads(r.read())                     # also runs in the dashboard's Python (no requests)
        _health[provider] = {"ok": True, "ms": int((time.time() - t0) * 1000), "at": time.time()}
        _cache[key] = (time.time(), data)
        return data
    except Exception as e:
        _health[provider] = {"ok": False, "error": f"{type(e).__name__}", "at": time.time()}
        if hit:                                   # stale but usable: serve it, marked by health
            return hit[1]
        raise


def _price(sym: str) -> float | None:
    try:
        return float(_get("binance", "https://api.binance.com/api/v3/ticker/price", symbol=f"{sym}USDT")["price"])
    except Exception:
        return None


def liquidations(sym: str = "BTC") -> dict:
    try:
        d = _get("marginpad", "https://marginpad.io/api/v1/liquidations/recent", symbol=sym, minutes=1440)
        b = d.get("buckets") or []
        px = _price(sym)
        longs = sum(x.get("long", 0) for x in b)
        shorts = sum(x.get("short", 0) for x in b)
        near = [x for x in b if px and abs(x["price"] / px - 1) <= 0.05]
        below = max((x for x in near if x["price"] < px), key=lambda x: x.get("long", 0) + x.get("short", 0), default=None)
        above = max((x for x in near if x["price"] > px), key=lambda x: x.get("long", 0) + x.get("short", 0), default=None)
        return {"long_usd_24h": round(longs), "short_usd_24h": round(shorts),
                "long_share": round(longs / (longs + shorts), 3) if longs + shorts else None, "price": px,
                "cluster_below": below and {"price": below["price"], "usd": round(below["long"] + below["short"])},
                "cluster_above": above and {"price": above["price"], "usd": round(above["long"] + above["short"])}}
    except Exception as e:
        return {"error": type(e).__name__}


def crowd(top: int = 12) -> dict:
    try:
        coins = _get("marginpad", "https://marginpad.io/api/v1/long-short")["data"]["coins"]
        rows = sorted(coins, key=lambda c: abs(c["longPct"] - 50), reverse=True)[:top]
        return {"coins": [{"coin": c["s"], "long_pct": c["longPct"], "venues": c.get("n")} for c in rows],
                "crowded_long": [c["s"] for c in coins if c["longPct"] >= 70],
                "crowded_short": [c["s"] for c in coins if c["longPct"] <= 30]}
    except Exception as e:
        return {"error": type(e).__name__}


def whales(top: int = 8) -> dict:
    out: dict = {}
    try:
        w = _get("coinlobster", "https://coinlobster.com/api/public/hyperliquid/whales").get("whales") or []
        w = sorted(w, key=lambda x: x.get("amountUsd", 0), reverse=True)[:top]
        out["hl_trades"] = [{"coin": x.get("coin"), "side": "BUY" if x.get("isBuy") else "SELL",
                             "usd": round(x.get("amountUsd", 0)), "wallet": (x.get("wallet") or "")[:10]} for x in w]
    except Exception as e:
        out["hl_trades_error"] = type(e).__name__
    try:
        rows = _get("coinlobster", "https://coinlobster.com/api/public/whale-radar").get("rows") or []
        out["radar"] = [{"coin": r["coin"], "direction": r.get("direction") or ("buy" if r.get("netUsd", 0) > 0 else "sell"),
                         "unusual": bool(r.get("unusual")), "net_usd": r.get("netUsd"), "count": r.get("count")}
                        for r in rows[:15]]
    except Exception as e:
        out["radar_error"] = type(e).__name__
    return out


def funding(asset: str = "BTC") -> dict:
    try:
        a = _get("optimistfi", f"https://optimistfi.com/api/perps/assets/{asset}")["asset"]
        hl = next((v for v in a.get("venues", []) if "hyperliquid" in str(v.get("venue", "")).lower()), None)
        return {"apr_avg": round(a.get("fundingApr") or 0, 4), "spread_apr": round(a.get("fundingSpreadApr") or 0, 4),
                "cheapest_long": a.get("cheapestLong"), "richest_short": a.get("richestShort"),
                "venues": a.get("venueCount"), "oi_usd": round(a.get("openInterestUsd") or 0),
                "hyperliquid_hourly": hl and hl.get("fundingRateHourly")}
    except Exception as e:
        return {"error": type(e).__name__}


def news(limit: int = 15) -> list[dict]:
    """Crypto Vision headlines, same shape as NewsAgent items: {title, link, published, source, credibility}."""
    try:
        arts = _get("cryptovision", "https://cryptocurrency.cv/api/news", limit=limit).get("articles") or []
        return [{"title": a.get("title"), "link": a.get("link"), "published": a.get("pubDate"),
                 "source": f"CryptoVision/{a.get('source')}", "credibility": a.get("credibility")} for a in arts]
    except Exception:
        return []


def api_health() -> dict:
    return {k: dict(v, age_s=int(time.time() - v["at"])) for k, v in _health.items()}


def run() -> dict:
    data = {"at": time.time(), "mode": "mesure (aucun poids)",
            "liquidations": {s: liquidations(s) for s in MAJORS}, "crowd": crowd(), "whales": whales(),
            "funding": {s: funding(s) for s in MAJORS}, "news": news(), "health": api_health()}
    try:
        tmp = OUT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(OUT)
    except Exception:
        pass
    return data


def read() -> dict:
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return {}


def start(every_s: int = 300) -> threading.Thread:
    def loop():
        while True:
            try:
                run()
            except Exception:
                pass
            time.sleep(every_s)
    t = threading.Thread(target=loop, name="MarketContext", daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    d = run()
    print(json.dumps({k: (v if k != "news" else v[:2]) for k, v in d.items()}, ensure_ascii=False, indent=1)[:3500])
