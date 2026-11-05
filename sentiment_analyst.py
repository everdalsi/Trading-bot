"""SentimentAnalyst (agents v2, B5) -- MEASUREMENT MODE: recorded next to every decision, no weight yet.

Reason to exist: 4 sentiment sources already run (NewsAgent, Polymarket analyst, social_listener, funding) but
each is read alone or not at all; social_listener and stock_sentiment never change a decision and were never
measured. One score per coin lets the Professor test whether crowd mood predicts our results.
Frame: reads public data + the files the other agents already write (news_flags.json, polymarket.json); never
trades, never vetoes. Every 15 min.
Function: DATA_DIR/sentiment.json = {at, market: {fear_greed, label}, coins: {SYM: {score -1..+1, parts}}} and a
SIGNAL in agent_events when a held coin's score moves by >= 0.4.

Parts per coin (each -1..+1), then a plain average of the available ones:
  fear_greed  market-wide Fear & Greed index (alternative.me), CONTRARIAN: extreme fear (+), extreme greed (-)
  funding     the coin's perpetual funding rate, contrarian: crowded longs (-), crowded shorts (+)
  news        NewsAgent strong flags of the last 24 h: positive (+1), negative (-1)
  polymarket  Polymarket implied sentiment (BTC/ETH/SOL...), as written by polymarket_analyst
"""
import json
import os
import threading
import time
from pathlib import Path

import requests

from agent_events import log_event

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "sentiment.json"
UA = {"User-Agent": "TradingResearchBot/1.0 paper-trading research (contact: bot-admin@example.org)"}
DEFAULT_COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


def fear_greed() -> dict:
    d = requests.get("https://api.alternative.me/fng/", params={"limit": 2}, headers=UA, timeout=15).json()["data"]
    v = int(d[0]["value"])
    return {"value": v, "label": d[0]["value_classification"], "yesterday": int(d[1]["value"]),
            "score": round(_clip((50 - v) / 40), 3)}          # contrarian


def funding_all() -> dict[str, float]:
    rows = requests.get("https://fapi.binance.com/fapi/v1/premiumIndex", headers=UA, timeout=15).json()
    return {r["symbol"]: float(r["lastFundingRate"]) for r in rows if r["symbol"].endswith("USDT")}


def held_coins() -> list[str]:
    """Coins held in the crypto books (copy + short term), plus the majors."""
    syms = set(DEFAULT_COINS)
    for f in ("hl_copy_ledger.json", "meme_sim.json"):
        for p in (_json(DATA_DIR / f).get("positions") or {}).values():
            s = p.get("symbol") or ""
            if s.endswith("USDT"):
                syms.add(s)
    return sorted(syms)


def run() -> dict:
    prev = _json(OUT).get("coins", {})
    fg = None
    try:
        fg = fear_greed()
    except Exception as e:
        log_event("SentimentAnalyst", "systeme", "INFO", "", f"Fear & Greed indisponible : {str(e)[:80]}")
    try:
        fund = funding_all()
    except Exception:
        fund = {}
    news = _json(DATA_DIR / "news_flags.json").get("symbols", {})
    poly = _json(DATA_DIR / "polymarket.json").get("coins", {})
    now = time.time()
    coins = {}
    for sym in held_coins():
        parts = {}
        if fg:
            parts["fear_greed"] = fg["score"]
        if sym in fund:
            parts["funding"] = round(_clip(-(fund[sym] - 0.0001) / 0.0005), 3)
        flags = [f for f in news.get(sym, []) + news.get(sym.replace("USDT", ""), [])
                 if f.get("level") in ("negatif_fort", "positif_fort") and now - float(f.get("at", 0)) < 86400]
        if flags:
            parts["news"] = round(sum(1 if f["level"] == "positif_fort" else -1 for f in flags) / len(flags), 3)
        ps = (poly.get(sym) or {}).get("sentiment")
        if ps is not None:
            parts["polymarket"] = round(float(ps), 3)
        if not parts:
            continue
        score = round(sum(parts.values()) / len(parts), 3)
        coins[sym] = {"score": score, "parts": parts}
        old = (prev.get(sym) or {}).get("score")
        if old is not None and abs(score - old) >= 0.4:
            log_event("SentimentAnalyst", "infos", "SIGNAL", sym, f"sentiment {old:+.2f} -> {score:+.2f} "
                      f"({', '.join(f'{k} {v:+.2f}' for k, v in parts.items())})", score=score)
    data = {"at": now, "market": fg, "coins": coins, "mode": "mesure (aucun poids)"}
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    return data


def read(symbol: str) -> dict:
    """For the SeniorExpert: the coin's sentiment (recorded with each decision, no weight)."""
    d = _json(OUT)
    c = (d.get("coins") or {}).get(symbol) or {}
    return {"sentiment": c.get("score"), "fear_greed": (d.get("market") or {}).get("value")}


_started = False


def start(every_s: int = 900) -> None:
    global _started
    if _started or not int(os.environ.get("SENTIMENT_ANALYST_ENABLED", 1)):
        return
    _started = True

    def loop():
        time.sleep(180)
        while True:
            try:
                run()
            except Exception as e:
                log_event("SentimentAnalyst", "systeme", "INFO", "", f"erreur interne {type(e).__name__}: {str(e)[:150]}")
            time.sleep(every_s)

    threading.Thread(target=loop, daemon=True, name="sentiment_analyst").start()


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, ensure_ascii=False)[:2000])
