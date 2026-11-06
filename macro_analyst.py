"""MacroAnalyst (agents v2, B4) -- MEASUREMENT MODE: recorded next to every decision, no weight yet.

Reason to exist: the bot's only macro view was BTC vs its 50-day average (now regime.py). Crypto and US
stocks also move with leverage (funding, open interest), the dollar, the S&P 500 and scheduled US events
(FOMC, CPI, jobs report), which none of the live agents read.
Frame: public data only, every 30 min, never trades, never vetoes (measurement first, user rule: a signal
gets weight only after it proved itself on >= 100 decisions or 2 weeks). Does NOT recompute the BTC regime
(that is regime.py, single source).
Function: writes DATA_DIR/macro.json = {at, score -1..+1 (+ = risk-on), components, blackout, next_event}
and a SIGNAL in agent_events when the score crosses +-0.3 or a high-impact US event is within 30 min.

Components (each -1..+1, + = supportive for risk assets):
  funding    BTC+ETH perpetual funding: very positive = crowded longs = fragile (-), negative = (+)
  oi_trend   24 h change of BTC open interest with price direction: price up + OI up = healthy (+)
  dominance  BTC dominance rising = money leaving alts (-) for the alt-heavy books
  dollar     DXY 5-day change: dollar up = risk-off (-)
  equities   S&P 500 5-day change: up = risk-on (+)
  stablecoins USDT+USDC supply 7-day change (DefiLlama): growing = new money in (+)
"""
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from agent_events import log_event

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "macro.json"
UA = {"User-Agent": "TradingResearchBot/1.0 paper-trading research (contact: bot-admin@example.org)"}
FAPI = "https://fapi.binance.com"
WEIGHTS = {"funding": 0.25, "oi_trend": 0.20, "dominance": 0.15, "dollar": 0.20, "equities": 0.20,
           "stablecoins": 0.15}
BLACKOUT_MIN = 30


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


def _get(url: str, **params):
    r = requests.get(url, params=params or None, headers=UA, timeout=15)
    r.raise_for_status()
    return r.json()


def funding() -> tuple[float, dict]:
    rates = {s: float(_get(f"{FAPI}/fapi/v1/premiumIndex", symbol=s)["lastFundingRate"]) for s in ("BTCUSDT", "ETHUSDT")}
    avg = sum(rates.values()) / len(rates)            # per 8 h; neutral ~0.01 %
    return _clip(-(avg - 0.0001) / 0.0004), {"rates_8h_pct": {k: round(v * 100, 4) for k, v in rates.items()}}


def oi_trend() -> tuple[float, dict]:
    oi = _get(f"{FAPI}/futures/data/openInterestHist", symbol="BTCUSDT", period="1h", limit=25)
    k = _get(f"{FAPI}/fapi/v1/klines", symbol="BTCUSDT", interval="1h", limit=25)
    d_oi = float(oi[-1]["sumOpenInterest"]) / float(oi[0]["sumOpenInterest"]) - 1
    d_px = float(k[-1][4]) / float(k[0][4]) - 1
    # price and OI in the same direction = conviction in that direction; OI up while price falls = shorts piling in
    s = _clip((d_px * 20) * (1 + min(abs(d_oi) * 10, 1)) if d_oi > 0 else d_px * 10)
    return s, {"oi_24h_pct": round(d_oi * 100, 2), "btc_24h_pct": round(d_px * 100, 2)}


def dominance() -> tuple[float, dict]:
    g = _get("https://api.coingecko.com/api/v3/global")["data"]
    dom = float(g["market_cap_percentage"]["btc"])
    prev = _json(OUT).get("components", {}).get("dominance", {}).get("btc_dominance_pct")
    change = 0.0 if prev is None else dom - float(prev)
    return _clip(-change / 0.5), {"btc_dominance_pct": round(dom, 2), "change_since_last_pp": round(change, 3),
                                  "mcap_change_24h_pct": round(float(g.get("market_cap_change_percentage_24h_usd", 0)), 2)}


def stablecoins() -> tuple[float, dict]:
    """USDT + USDC circulating supply over 7 days (DefiLlama, free, no key; found via the public-apis list 06/11).
    Supply growing = new dollars entering crypto (+), shrinking = money leaving (-). +-1 % in 7 d = full score."""
    out = {}
    for name, sid in (("usdt", 1), ("usdc", 2)):
        rows = _get("https://stablecoins.llama.fi/stablecoincharts/all", stablecoin=sid)
        now, before = rows[-1]["totalCirculatingUSD"]["peggedUSD"], rows[-8]["totalCirculatingUSD"]["peggedUSD"]
        out[name] = (now, before)
    now, before = sum(v[0] for v in out.values()), sum(v[1] for v in out.values())
    ch = now / before - 1
    return _clip(ch / 0.01), {"supply_bn": round(now / 1e9, 1), "supply_7d_pct": round(ch * 100, 2),
                              **{f"{k}_7d_pct": round((v[0] / v[1] - 1) * 100, 2) for k, v in out.items()}}


def _yahoo_5d(symbol: str) -> float:
    r = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}", range="10d", interval="1d")
    closes = [c for c in r["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c is not None]
    return closes[-1] / closes[-6] - 1 if len(closes) >= 6 else 0.0


def dollar() -> tuple[float, dict]:
    ch = _yahoo_5d("DX-Y.NYB")
    return _clip(-ch / 0.015), {"dxy_5d_pct": round(ch * 100, 2)}


def equities() -> tuple[float, dict]:
    ch = _yahoo_5d("^GSPC")
    return _clip(ch / 0.03), {"spx_5d_pct": round(ch * 100, 2)}


def calendar() -> dict:
    """High-impact US events this week (public weekly calendar feed); blackout = within 30 min of one."""
    try:
        rows = _get("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
    except Exception as e:
        return {"error": str(e)[:80], "blackout": False, "next_event": None}
    now = datetime.now(timezone.utc)
    events = []
    for r in rows:
        if r.get("country") != "USD" or r.get("impact") != "High":
            continue
        try:
            t = datetime.fromisoformat(r["date"]).astimezone(timezone.utc)
        except Exception:
            continue
        events.append((t, r.get("title", "")))
    events.sort()
    nxt = next(((t, n) for t, n in events if t >= now), None)
    blackout = any(abs((t - now).total_seconds()) <= BLACKOUT_MIN * 60 for t, _ in events)
    return {"blackout": blackout, "next_event": {"at": nxt[0].isoformat(), "title": nxt[1]} if nxt else None,
            "events_this_week": len(events)}


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run() -> dict:
    prev = _json(OUT)
    comps, parts = {}, {}
    for name, fn in (("funding", funding), ("oi_trend", oi_trend), ("dominance", dominance), ("dollar", dollar),
                     ("equities", equities), ("stablecoins", stablecoins)):
        try:
            s, detail = fn()
            parts[name] = s
            comps[name] = {"score": round(s, 3), **detail}
        except Exception as e:
            comps[name] = {"error": f"{type(e).__name__}: {str(e)[:80]}"}
    w = sum(WEIGHTS[k] for k in parts)
    score = round(sum(WEIGHTS[k] * v for k, v in parts.items()) / w, 3) if w else None
    cal = calendar()
    data = {"at": time.time(), "score": score, "label": None if score is None else
            "risk_on" if score >= 0.3 else "risk_off" if score <= -0.3 else "neutre",
            "components": comps, "blackout": cal.get("blackout", False), "next_event": cal.get("next_event"),
            "calendar": cal, "mode": "mesure (aucun poids)"}
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    old = prev.get("label")
    if data["label"] and data["label"] != old:
        log_event("MacroAnalyst", "marche", "SIGNAL", "", f"macro {old or '?'} -> {data['label']} (score {score:+.2f})",
                  score=score, result=data["label"])
    if data["blackout"] and not prev.get("blackout"):
        ev = data["next_event"] or {}
        log_event("MacroAnalyst", "marche", "SIGNAL", "", f"annonce US forte dans moins de {BLACKOUT_MIN} min : "
                  f"{ev.get('title', '?')}", result="blackout")
    return data


def read() -> dict:
    """For the SeniorExpert: the latest macro view (recorded with each decision, no weight)."""
    d = _json(OUT)
    return {"macro_score": d.get("score"), "macro_label": d.get("label"), "macro_blackout": d.get("blackout")}


_started = False


def start(every_s: int = 1800) -> None:
    global _started
    if _started or not int(os.environ.get("MACRO_ANALYST_ENABLED", 1)):
        return
    _started = True

    def loop():
        time.sleep(120)
        while True:
            try:
                run()
            except Exception as e:
                log_event("MacroAnalyst", "systeme", "INFO", "", f"erreur interne {type(e).__name__}: {str(e)[:150]}")
            time.sleep(every_s)

    threading.Thread(target=loop, daemon=True, name="macro_analyst").start()


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, ensure_ascii=False))
