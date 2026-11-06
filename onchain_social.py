"""On-chain flows + calendar (listings) + free social signal (desk spec 06/11, user: free sources first). Stdlib only.

  whale_flows(hours=6)  Whale Alert public Telegram channel (t.me/s/whale_alert_io, public web preview, no account):
                        big transfers TO exchanges (sell pressure) / FROM exchanges (accumulation) per coin, stablecoin
                        mints (fresh buying power). -> {coin: {to_exchange_usd, from_exchange_usd, net_usd}, mints_usd}
  listings(n=10)        Binance "New Cryptocurrency Listing" announcements (public CMS JSON): coins about to list
                        (calendar agent: listings often move price; pump risk for the manipulation detector)
  mentions(channels)    public Telegram news channels (web preview): coin mentions in the last hours + a small
                        bullish/bearish lexicon -> attention and tone per coin (free social sentiment)
Not covered for free (noted for the user): token unlock calendars (DefiLlama emissions = paid plan), Reddit (blocks
anonymous clients; needs app keys), X (paid API). Polymarket resolutions are read on the VPS (gamma API not reachable
from this PC). Low rate: one fetch per source every 10 min, cached.
"""
import html
import json
import re
import time
import urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "Mozilla/5.0 (paper-trading research bot)"}
EXCHANGES = ("binance", "coinbase", "kraken", "bitfinex", "okx", "okex", "bybit", "kucoin", "bitstamp", "gemini",
             "htx", "huobi", "gate", "bitget", "crypto.com", "upbit", "robinhood")
STABLES = ("USDT", "USDC", "DAI", "FDUSD", "PYUSD")
BULL = re.compile(r"\b(surge|soar|rall(y|ies)|bull|breakout|record high|ath|approv|inflow|buy(s|ing)?|adopt|partnership|launch)\w*", re.I)
BEAR = re.compile(r"\b(crash|plunge|dump|bear|hack|exploit|lawsuit|sec charges|outflow|sell(s|ing)?-?off|liquidat|ban|delist|fraud)\w*", re.I)
_cache: dict[str, tuple[float, object]] = {}


def _fetch(url: str, ttl: float = 600) -> str:
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
        text = r.read().decode("utf-8", "replace")
    _cache[url] = (time.time(), text)
    return text


def _tg_messages(channel: str) -> list[tuple[datetime, str]]:
    page = _fetch(f"https://t.me/s/{channel}")
    out = []
    for block in page.split('class="tgme_widget_message_wrap')[1:]:          # one chunk per message
        t = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', block, re.S)
        d = re.search(r'datetime="([^"]+)"', block)
        if t and d:
            text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t.group(1)))).strip()
            out.append((datetime.fromisoformat(d.group(1)), text))
    return out


def whale_flows(hours: float = 6) -> dict:
    try:
        since = datetime.now(timezone.utc).timestamp() - hours * 3600
        flows: dict[str, dict] = {}
        mints = 0.0
        n = 0
        for when, text in _tg_messages("whale_alert_io"):
            if when.timestamp() < since:
                continue
            m = re.search(r"([\d,.]+)\s+\$?#?([A-Z0-9]{2,10})\s+\(([\d,]+)\s+USD\)\s+(transferred|minted|burned)(.*)", text)
            if not m:
                continue
            n += 1
            coin, usd, kind, rest = m.group(2), float(m.group(3).replace(",", "")), m.group(4), m.group(5).lower()
            if kind == "minted" and coin in STABLES:
                mints += usd
                continue
            frm = re.search(r"from (.*?) to", rest)
            to = re.search(r" to (.*)", rest)
            frm_ex = bool(frm and any(e in frm.group(1) for e in EXCHANGES))
            to_ex = bool(to and any(e in to.group(1) for e in EXCHANGES))
            f = flows.setdefault(coin, {"to_exchange_usd": 0.0, "from_exchange_usd": 0.0})
            if to_ex and not frm_ex:
                f["to_exchange_usd"] += usd
            elif frm_ex and not to_ex:
                f["from_exchange_usd"] += usd
        for f in flows.values():
            f["net_usd"] = round(f["from_exchange_usd"] - f["to_exchange_usd"])     # > 0 = leaving exchanges (bullish)
            f["to_exchange_usd"], f["from_exchange_usd"] = round(f["to_exchange_usd"]), round(f["from_exchange_usd"])
        return {"hours": hours, "alerts": n, "coins": flows, "stable_mints_usd": round(mints)}
    except Exception as e:
        return {"error": type(e).__name__}


def listings(n: int = 10) -> list[dict]:
    try:
        d = json.loads(_fetch("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
                              f"?type=1&catalogId=48&pageNo=1&pageSize={n}", ttl=1800))
        arts = d["data"]["catalogs"][0]["articles"]
        out = []
        for a in arts:
            coins = re.findall(r"\(([A-Z0-9]{2,10})\)", a["title"])
            out.append({"title": a["title"], "coins": coins,
                        "announced": datetime.fromtimestamp(a["releaseDate"] / 1000, timezone.utc).isoformat()})
        return out
    except Exception:
        return []


def mentions(coins: list[str], channels: tuple[str, ...] = ("cointelegraph", "WatcherGuru", "whale_alert_io"),
             hours: float = 12) -> dict:
    since = datetime.now(timezone.utc).timestamp() - hours * 3600
    res: dict[str, dict] = {c: {"mentions": 0, "bull": 0, "bear": 0} for c in coins}
    for ch in channels:
        try:
            for when, text in _tg_messages(ch):
                if when.timestamp() < since:
                    continue
                for c in coins:
                    if re.search(rf"(?<![A-Za-z0-9])\$?{re.escape(c)}(?![A-Za-z0-9])", text):
                        r = res[c]
                        r["mentions"] += 1
                        r["bull"] += bool(BULL.search(text))
                        r["bear"] += bool(BEAR.search(text))
        except Exception:
            continue
    for r in res.values():
        r["tone"] = round((r["bull"] - r["bear"]) / r["mentions"], 2) if r["mentions"] else None
    return {"hours": hours, "channels": list(channels), "coins": res}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(whale_flows(), indent=1)[:1200])
    for x in listings(5):
        print(x)
    print(json.dumps(mentions(["BTC", "ETH", "SOL", "XRP", "HYPE", "TRUMP"]), indent=1)[:900])
