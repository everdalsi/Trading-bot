"""Polymarket analyst (user brief §3), READ-ONLY: public Gamma API probabilities used as a crypto
sentiment signal. It never places a bet (Polymarket is not available from Australia, and this bot
is paper only). The old agents/polymarket_*.py are not wired anywhere; this one replaces their
signal part.

Every 30 min, from the most traded open markets:
- "Will the price of <coin> be above $K on <date>?" -> P(price > K) at that date. On the nearest date,
  the implied median is where P crosses 0.5 (linear interpolation between the two thresholds that
  bracket 50 %; no bracket = no sentiment, never a guess).
  sentiment = (implied median / spot - 1) / 2 %, clamped to [-1, +1]  (2 % above spot = +1)
- "Will <coin> dip to $K by <date>?" -> crash_prob = highest probability of a fall of >= 15 %.
Output DATA_DIR/polymarket.json {at, coins: {BTCUSDT: {spot, implied_median, sentiment, crash_prob,
date, markets}}} and a SIGNAL event when a coin's sentiment moves by >= 0.3.

First stage: recorded next to the SeniorExpert decisions (field polymarket) so the Professor can
measure whether it predicts anything, BEFORE it is given any weight.
"""
import json
import os
import re
import threading
import time
from pathlib import Path

import requests

from agent_events import log_event

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "polymarket.json"
GAMMA = "https://gamma-api.polymarket.com/markets"
COINS = {"bitcoin": "BTCUSDT", "btc": "BTCUSDT", "ethereum": "ETHUSDT", "eth": "ETHUSDT",
         "solana": "SOLUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}
_ABOVE = re.compile(r"price of (bitcoin|ethereum|solana|xrp|btc|eth|sol) be above \$([\d,.]+k?) on ([a-z]+ \d+)", re.I)
_DIP = re.compile(r"(bitcoin|ethereum|solana|xrp|btc|eth|sol) dip to \$([\d,.]+k?)", re.I)


def _num(s: str) -> float:
    s = s.lower().replace(",", "")
    return float(s[:-1]) * 1000 if s.endswith("k") else float(s)


def _spot() -> dict[str, float]:
    rows = requests.get("https://api.binance.com/api/v3/ticker/price", timeout=10).json()
    want = set(COINS.values())
    return {r["symbol"]: float(r["price"]) for r in rows if r["symbol"] in want}


def run() -> dict:
    markets = requests.get(GAMMA, params={"active": "true", "closed": "false", "limit": 500,
                                          "order": "volume24hr", "ascending": "false"}, timeout=30).json()
    spot = _spot()
    above: dict[str, dict[str, list]] = {}
    dips: dict[str, list] = {}
    for m in markets:
        q = m.get("question", "")
        try:
            p_yes = float(json.loads(m.get("outcomePrices") or "[]")[0])
        except (ValueError, IndexError, TypeError):
            continue
        if (a := _ABOVE.search(q)):
            sym = COINS[a.group(1).lower()]
            above.setdefault(sym, {}).setdefault(a.group(3).lower(), []).append((_num(a.group(2)), p_yes, q))
        elif (d := _DIP.search(q)):
            dips.setdefault(COINS[d.group(1).lower()], []).append((_num(d.group(2)), p_yes, q))
    old = {}
    try:
        old = json.loads(OUT.read_text(encoding="utf-8")).get("coins", {})
    except (OSError, ValueError):
        pass
    coins = {}
    for sym, px in spot.items():
        rec = {"spot": px, "implied_median": None, "sentiment": None, "crash_prob": None, "date": None, "markets": []}
        dates = above.get(sym, {})
        if dates:
            date = min(dates, key=lambda d: len(dates[d]) * -1)  # the date with the most thresholds
            pts = sorted(dates[date])  # (threshold, P(above))
            rec["date"], rec["markets"] = date, [q for _, _, q in pts][:6]
            med = None
            for (k1, p1, _), (k2, p2, _) in zip(pts, pts[1:]):
                if p1 >= 0.5 >= p2 and p1 != p2:
                    med = k1 + (p1 - 0.5) / (p1 - p2) * (k2 - k1)
                    break
            # only when two thresholds bracket 50 %: with all thresholds on one side the median is
            # unknown (e.g. BTC with only "above 82k: 4 %" and "above 84k: 0.6 %" says nothing about
            # whether it is above or below spot) -> no sentiment rather than a wrong one
            if med is not None:
                rec["implied_median"] = round(med, 2)
                rec["sentiment"] = round(max(-1.0, min(1.0, (med / px - 1) / 0.02)), 3)
        crash = [p for k, p, _ in dips.get(sym, []) if k <= px * 0.85]
        if crash:
            rec["crash_prob"] = round(max(crash), 3)
        coins[sym] = rec
        prev = (old.get(sym) or {}).get("sentiment")
        if rec["sentiment"] is not None and (prev is None or abs(rec["sentiment"] - prev) >= 0.3):
            log_event("PolymarketAnalyst", "crypto", "SIGNAL", sym.replace("USDT", ""),
                      f"mediane implicite {rec['implied_median']:,} $ vs {px:,.0f} $ ({rec['date']})",
                      score=rec["sentiment"], result="haussier" if rec["sentiment"] > 0.2 else
                      "baissier" if rec["sentiment"] < -0.2 else "neutre", crash_prob=rec["crash_prob"])
    data = {"at": time.time(), "coins": coins}
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(OUT)
    return data


def sentiment(symbol: str) -> dict | None:
    try:
        d = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if time.time() - d.get("at", 0) > 3 * 3600:
        return None
    return d.get("coins", {}).get(symbol if symbol.endswith("USDT") else symbol + "USDT")


_started = False


def start(every_s: int = 1800) -> None:
    global _started
    if _started or not int(os.environ.get("POLYMARKET_ANALYST_ENABLED", 1)):
        return
    _started = True

    def loop():
        time.sleep(200)
        while True:
            try:
                run()
            except Exception as e:
                log_event("PolymarketAnalyst", "crypto", "INFO", "", f"erreur {type(e).__name__}: {str(e)[:150]}")
            time.sleep(every_s)

    threading.Thread(target=loop, daemon=True, name="polymarket_analyst").start()
