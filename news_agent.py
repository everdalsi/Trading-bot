"""NewsAgent (spec B of ~/.claude/plans/trading-moteur-gestion-et-infos.md): RSS headlines around
the symbols we hold, every 10 min. Keywords only on the VPS (the local LLM runs on the user's PC);
headlines that are neither clearly negative nor positive are kept as "ambigu" for the dashboard.

Sources: crypto = CoinDesk, Cointelegraph, The Block, Decrypt + Google News "<token> crypto";
stocks = Yahoo Finance per ticker, Google News "<ticker> stock", SEC EDGAR 8-K filings.
A headline belongs to a symbol when the ticker or one of its aliases appears in the title.

Output DATA_DIR/news_flags.json = {"at", "symbols": {SYM: [{at, level, title, source, link}]}}
(level: negatif_fort | positif_fort | ambigu ; last 24 h), and one SIGNAL line in agent_events per
new strong flag. Used by TradeManager R5 and by the SeniorExpert (no entry < 6 h after a strong
negative flag on the symbol).
"""
import calendar
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote

import feedparser
import requests

from agent_events import log_event

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "news_flags.json"
UA = {"User-Agent": "trading-research-bot/1.0 (paper trading, contact via repository owner)"}
CRYPTO_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "The Block": "https://www.theblock.co/rss.xml",
    "Decrypt": "https://decrypt.co/feed",
}
ALIASES = {
    "BTC": ["bitcoin"], "ETH": ["ethereum", "ether"], "SOL": ["solana"], "XRP": ["ripple", "xrp"],
    "DOGE": ["dogecoin"], "AVAX": ["avalanche"], "LINK": ["chainlink"], "ADA": ["cardano"],
    "DOT": ["polkadot"], "LTC": ["litecoin"], "SUI": ["sui"], "APT": ["aptos"], "ARB": ["arbitrum"],
    "OP": ["optimism"], "NEAR": ["near protocol"], "TRX": ["tron"], "ATOM": ["cosmos"], "TON": ["toncoin"],
    "ICP": ["internet computer"], "FIL": ["filecoin"], "UNI": ["uniswap"], "AAVE": ["aave"], "CRV": ["curve"],
    "INJ": ["injective"], "WLD": ["worldcoin"], "TAO": ["bittensor"], "BNB": ["bnb", "binance coin"],
    "HYPE": ["hyperliquid"], "ZEC": ["zcash"], "PEPE": ["pepe"],
    "AAPL": ["apple"], "MSFT": ["microsoft"], "NVDA": ["nvidia"], "AMZN": ["amazon"], "GOOGL": ["alphabet", "google"],
    "KO": ["coca-cola"], "AXP": ["american express"], "BAC": ["bank of america"], "PLTR": ["palantir"],
    "SPY": ["s&p 500"],
}
NEG = [r"hack(ed|er|ers)?", r"exploit(ed)?", r"drain(ed)?", r"delist(s|ed|ing)?", r"remov(es|ed) .*listing",
       r"sec (charges|sues)", r"lawsuit", r"sued", r"bankrupt(cy)?", r"insolven(t|cy)", r"rug ?pull", r"\brug\b",
       r"halts? withdrawals", r"depeg(ged)?", r"security breach", r"fraud", r"ponzi", r"indict(ed|ment)",
       r"class action", r"trading halt(ed)?", r"suspend(s|ed) trading", r"retrait de cotation",
       r"\bban(s|ned)?\b", r"(network|chain|blockchain|mainnet) (halt(s|ed)?|outage|down|stall(s|ed)?)", r"51% attack"]
POS = [r"\blists\b", r"listing on", r"etf (approved|approval)", r"approves? .*etf", r"partnership with",
       r"strategic partnership", r"beats? (estimates|expectations)", r"earnings beat", r"record revenue",
       r"raises? guidance"]
AMBIG = [r"investigat", r"probe", r"outage", r"warn(s|ing)", r"downgrad", r"upgrad", r"surge", r"plunge",
         r"crash", r"soar", r"slump", r"sell-?off", r"rall(y|ies)", r"whale", r"liquidat"]
_NEG, _POS, _AMB = (re.compile("|".join(x), re.I) for x in (NEG, POS, AMBIG))
_LOCK = threading.Lock()


MAJORS = {"BTC", "ETH"}  # hit by third-party hacks (exchanges, protocols) without being affected themselves
_MAJOR_NEG = re.compile(r"(bitcoin|ethereum|btc|eth) (network|protocol|blockchain|core)|ban(s|ned)?|51% attack", re.I)


def classify(title: str, symbol: str = "") -> str:
    if _NEG.search(title):
        if symbol.replace("USDT", "") in MAJORS and not _MAJOR_NEG.search(title):
            return "ambigu"  # e.g. "Mercado Bitcoin hack": an exchange, not Bitcoin itself
        return "negatif_fort"
    if _POS.search(title):
        return "positif_fort"
    if _AMB.search(title):
        return "ambigu"
    return ""


def _terms(symbol: str) -> list[str]:
    base = symbol.replace("USDT", "")
    return [base] + ALIASES.get(base, [])


def mentions(title: str, symbol: str) -> bool:
    for t in _terms(symbol):
        if len(t) <= 3 and t.isupper() or t == symbol.replace("USDT", ""):
            if re.search(rf"(?<![A-Za-z]){re.escape(t)}(?![A-Za-z])", title) or f"${t}" in title:
                return True
        elif re.search(rf"\b{re.escape(t)}\b", title, re.I):
            return True
    return False


def _entries(url: str, source: str) -> list[dict]:
    try:
        r = requests.get(url, headers=UA, timeout=20)
        feed = feedparser.parse(r.content)
    except Exception:
        return []
    out = []
    for e in feed.entries[:60]:
        ts = e.get("published_parsed") or e.get("updated_parsed")
        at = calendar.timegm(ts) if ts else time.time()
        out.append({"title": e.get("title", ""), "link": e.get("link", ""), "source": source, "at": at})
    return out


def collect(symbols: list[str]) -> list[tuple[str, dict]]:
    """(symbol, item) pairs from every source, last 24 h."""
    items = []
    crypto = [s for s in symbols if s.endswith("USDT")]
    stocks = [s for s in symbols if not s.endswith("USDT")]
    if crypto:
        for name, url in CRYPTO_FEEDS.items():
            items += _entries(url, name)
        for s in crypto:
            q = quote(f"{ALIASES.get(s[:-4], [s[:-4]])[0]} crypto")
            items += _entries(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en", "Google News")
    for s in stocks:
        items += _entries(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={s}&region=US&lang=en-US", "Yahoo Finance")
        items += _entries(f"https://news.google.com/rss/search?q={quote(s + ' stock')}&hl=en-US&gl=US&ceid=US:en", "Google News")
        items += [{**i, "title": f"{s} 8-K: {i['title']}"} for i in _entries(
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={s}&type=8-K&dateb=&owner=include&count=10&output=atom",
            "SEC EDGAR")]
    cutoff = time.time() - 86400
    pairs = []
    for it in items:
        if it["at"] < cutoff:
            continue
        for s in symbols:
            if it["source"] == "SEC EDGAR" and not it["title"].startswith(f"{s} 8-K"):
                continue
            if it["source"] == "SEC EDGAR" or mentions(it["title"], s):
                pairs.append((s, it))
    return pairs


def load() -> dict:
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"at": 0, "symbols": {}}


def run(symbols: list[str], held: set[str] | None = None) -> dict:
    held = held or set(symbols)
    old = load().get("symbols", {})
    seen = {f["link"] or f["title"] for flags in old.values() for f in flags}
    out: dict[str, list] = {}
    for s, it in collect(symbols):
        level = classify(it["title"], s)
        if not level:
            continue
        key = it["link"] or it["title"]
        flag = {"at": it["at"], "level": level, "title": it["title"][:200], "source": it["source"], "link": it["link"],
                "id": hashlib.md5(key.encode()).hexdigest()[:10]}
        lst = out.setdefault(s, [])
        if all(f["id"] != flag["id"] for f in lst):
            lst.append(flag)
        if level != "ambigu" and key not in seen and s in held:
            seen.add(key)
            log_event("NewsAgent", "infos", "SIGNAL", s, f"{it['source']} : {it['title'][:160]}",
                      result=level, link=it["link"])
    data = {"at": time.time(), "symbols": {s: sorted(v, key=lambda f: -f["at"])[:20] for s, v in out.items()}}
    with _LOCK:
        tmp = OUT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(OUT)
    return data


def strong_negative(symbol: str, within_s: float) -> str:
    """Title of a strong negative flag on this symbol within the window, else ''."""
    for f in load().get("symbols", {}).get(symbol, []):
        if f.get("level") == "negatif_fort" and time.time() - float(f.get("at", 0)) <= within_s:
            return f.get("title", "")
    return ""


_started = False


def start(every_s: int = 600) -> None:
    global _started
    if _started or not int(os.environ.get("NEWS_AGENT_ENABLED", 1)):
        return
    _started = True

    def loop():
        time.sleep(150)
        while True:
            try:
                import chart_analyst
                held = chart_analyst.held_symbols()
                run(sorted(held | {"BTCUSDT", "ETHUSDT"}), held)
            except Exception as e:
                log_event("NewsAgent", "infos", "INFO", "", f"erreur interne {type(e).__name__}: {e}")
            time.sleep(every_s)

    threading.Thread(target=loop, daemon=True, name="news_agent").start()
