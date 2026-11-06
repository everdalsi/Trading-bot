"""PublicData (06/11, user: "le public api devrait servir a avoir les donnees ... importantes pour les trades en
direct, il faut connecter un agent qui utilise public api pour le trading bot").

Every 15 min, for the 100 markets of the two universes (market_desk.universe("ALL")), free public data, no key:
- Binance USD-M futures (fapi.binance.com/futures/data, period 1h):
    topLongShortPositionRatio   share of the TOP traders' positions that are long (what the best accounts do)
    globalLongShortAccountRatio share of ALL accounts that are long (the crowd)
    takerlongshortRatio         aggressive buy volume / sell volume (who is pushing now)
    openInterestHist            open interest in USD and its 1 h change (money entering / leaving)
- fallback when Binance fails or the coin has no Binance perp: MarginPad free API (funding, open interest).
microstructure.py reads taker flow / OI on demand for the (stopped) 1-5 min book only; this agent is the
continuous source for the 100 markets, and adds the top-trader positioning nobody read before.
Output DATA_DIR/public_data.json {at, source, markets: {SYM: {universe, top_long_pct, all_long_pct, taker_buy_sell,
oi_usd, oi_chg_1h_pct, at}}}. Missing values stay missing (never a default that looks like a measurement).
Fail-open: if every source is down the file keeps its old "at" and readers see it is stale (load()).
Budget: 4 requests x 100 markets per round; Binance allows 1000 per 5 min per IP on these endpoints.
"""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "public_data.json"
FAPI = "https://fapi.binance.com/futures/data/"
MARGINPAD = "https://marginpad.io/api/v1/funding"
_started = False


def _get(url: str, params: dict | None = None):
    r = requests.get(url, params=params, timeout=15, headers={"User-Agent": "trading-bot-public-data"})
    r.raise_for_status()
    return r.json()


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def binance_metrics(sym: str) -> dict:
    """Top traders / crowd / taker flow / open interest for one perp, last closed hour. {} when not listed."""
    out = {}
    try:
        top = _get(FAPI + "topLongShortPositionRatio", {"symbol": sym, "period": "1h", "limit": 1})
        if top and _f(top[-1].get("longAccount")) is not None:
            out["top_long_pct"] = round(100 * _f(top[-1]["longAccount"]), 1)
        crowd = _get(FAPI + "globalLongShortAccountRatio", {"symbol": sym, "period": "1h", "limit": 1})
        if crowd and _f(crowd[-1].get("longAccount")) is not None:
            out["all_long_pct"] = round(100 * _f(crowd[-1]["longAccount"]), 1)
        tk = _get(FAPI + "takerlongshortRatio", {"symbol": sym, "period": "1h", "limit": 1})
        if tk and _f(tk[-1].get("buySellRatio")) is not None:
            out["taker_buy_sell"] = round(_f(tk[-1]["buySellRatio"]), 3)
        oi = _get(FAPI + "openInterestHist", {"symbol": sym, "period": "1h", "limit": 2})
        if len(oi) >= 2:
            a, b = _f(oi[-2].get("sumOpenInterestValue")), _f(oi[-1].get("sumOpenInterestValue"))
            if b is not None:
                out["oi_usd"] = round(b)
                if a:
                    out["oi_chg_1h_pct"] = round(100 * (b / a - 1), 2)
    except Exception:
        pass                     # not a Binance perp, or Binance refused: fallback / missing value applies
    return out


def marginpad() -> dict:
    """{COIN: {funding, oi_usd}} from MarginPad (fallback, one request for every coin)."""
    try:
        d = _get(MARGINPAD)
        return {c["s"].upper(): {"funding": c.get("funding"), "oi_usd": c.get("oiUsd")}
                for c in (d.get("data") or {}).get("coins", []) if c.get("s")}
    except Exception:
        return {}


def run_once(symbols: list[str], universe_of: dict | None = None) -> dict:
    with ThreadPoolExecutor(6) as ex:
        rows = dict(zip(symbols, ex.map(binance_metrics, symbols)))
    mp = None
    now = time.time()
    markets = {}
    for s, m in rows.items():
        if "oi_usd" not in m:                                   # no Binance perp data: try the fallback
            mp = marginpad() if mp is None else mp
            alt = mp.get(s[:-4]) or {}
            if alt.get("oi_usd") is not None:
                m = {**m, "oi_usd": round(alt["oi_usd"]), "funding_mp": alt.get("funding"), "source": "marginpad"}
        if m:
            markets[s] = {**m, "universe": (universe_of or {}).get(s), "at": now}
    data = {"at": now, "source": "binance futures public data (+ marginpad fallback)",
            "markets_n": len(markets), "asked_n": len(symbols), "markets": markets}
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(OUT)
    return data


def load(max_age_s: float = 3600) -> dict:
    """Markets of the last round, {} when the file is older than max_age_s (stale data is never used)."""
    try:
        d = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d.get("markets", {}) if time.time() - d.get("at", 0) < max_age_s else {}


def start(every_s: int = 900) -> None:
    global _started
    if _started or not int(os.environ.get("PUBLIC_DATA_ENABLED", 1)):
        return
    _started = True
    from agent_events import log_event

    def loop():
        time.sleep(60)
        while True:
            try:
                import market_desk
                a, b = market_desk.universe("A"), market_desk.universe("B")
                d = run_once(a + b, {**{s: "A" for s in a}, **{s: "B" for s in b}})
                log_event("PublicData", "analyse", "INFO", "",
                          f"donnees publiques : {d['markets_n']}/{d['asked_n']} marches (top traders, foule, flux, OI)")
            except Exception as e:
                log_event("PublicData", "analyse", "INFO", "", f"erreur interne {type(e).__name__}: {str(e)[:200]}")
            time.sleep(every_s)

    threading.Thread(target=loop, daemon=True, name="public_data").start()
