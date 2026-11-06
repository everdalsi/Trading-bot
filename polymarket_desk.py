"""PolymarketDesk: paper desk on Polymarket (user 06/11: "desk Polymarket papier oui").

MEASUREMENT BOOK, outside the 2,000 $ capital (no money moved, no account): it shows whether following abnormal
probability moves on liquid markets would make money, before any real use.
- Scanner every PM_EVERY_S on the public Gamma API (no key): active markets with liquidity >= PM_MIN_LIQ and
  24 h volume >= PM_MIN_VOL, ending in more than 24 h (live games excluded: their 1 h moves are the score).
- Signal: |1 h change of the first outcome| >= PM_MOVE (8 points) -> follow the move with a PM_STAKE $ paper bet,
  bought at the real best ask (first outcome) or at 1 - best bid (second outcome). Max PM_MAX_OPEN, one per market.
- Exits: market resolved (paid 1 or 0 from the final prices), or after PM_HOLD_H hours at the real best bid,
  or -50 % at the bid. Polymarket takes no trading fee on most markets: none is invented here.
-> DATA_DIR/polymarket_desk.json, events PolymarketDesk SIGNAL / BUY / CLOSE. Never raises.
"""
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
BOOK = DATA_DIR / "polymarket_desk.json"
GAMMA = "https://gamma-api.polymarket.com/markets"
EVERY_S = int(os.environ.get("PM_EVERY_S", 600))
MIN_LIQ, MIN_VOL = float(os.environ.get("PM_MIN_LIQ", 50000)), float(os.environ.get("PM_MIN_VOL", 100000))
MOVE, STAKE = float(os.environ.get("PM_MOVE", 0.08)), float(os.environ.get("PM_STAKE", 10))
MAX_OPEN, HOLD_H = int(os.environ.get("PM_MAX_OPEN", 15)), float(os.environ.get("PM_HOLD_H", 72))
START_CASH = 300.0


def _load() -> dict:
    try:
        return json.loads(BOOK.read_text(encoding="utf-8"))
    except Exception:
        return {"start": START_CASH, "cash": START_CASH, "positions": {}, "trades": [], "mode": "carnet de mesure, hors capital"}


def _save(b: dict) -> None:
    b["at"] = time.time()
    tmp = BOOK.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    tmp.replace(BOOK)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _market(mid: str) -> dict | None:
    try:
        r = requests.get(GAMMA, params={"id": mid}, timeout=15).json()
        return r[0] if r else None
    except Exception:
        return None


def _bid_of(m: dict, outcome: int) -> float | None:
    """Price we could SELL our share at now: best bid for outcome 0, 1 - best ask for outcome 1."""
    bb, ba = _f(m.get("bestBid")), _f(m.get("bestAsk"))
    if outcome == 0:
        return bb
    return 1 - ba if ba is not None else None


def run() -> dict:
    from agent_events import log_event
    b = _load()
    now = time.time()
    # 1) manage open bets
    for mid, p in list(b["positions"].items()):
        m = _market(mid)
        if not m:
            continue
        price, why = None, ""
        if m.get("closed"):
            try:
                final = json.loads(m.get("outcomePrices") or "[]")
                price, why = float(final[p["outcome"]]), "marche resolu"
            except Exception:
                continue
        else:
            bid = _bid_of(m, p["outcome"])
            if bid is None:
                continue
            p["last"] = bid
            if bid <= p["entry"] * 0.5:
                price, why = bid, "perte de 50 %"
            elif now - p["opened"] >= HOLD_H * 3600:
                price, why = bid, f"duree max {HOLD_H:.0f} h"
        if price is not None:
            value = p["shares"] * price
            pnl = value - p["usd"]
            b["cash"] += value
            b["trades"].append({**p, "exit": price, "pnl": round(pnl, 4), "closed": now, "reason": why})
            del b["positions"][mid]
            log_event("PolymarketDesk", "polymarket", "CLOSE", p["question"][:60], f"{why} : {p['label']} {p['entry']:.3f} -> {price:.3f}",
                      result=round(pnl, 4))
    # 2) scan for abnormal moves
    try:
        ms = requests.get(GAMMA, params={"active": "true", "closed": "false", "limit": 300, "order": "volume24hr",
                                         "ascending": "false"}, timeout=20).json()
    except Exception:
        ms = []
    signals = 0
    for m in ms:
        try:
            liq, vol, ch = _f(m.get("liquidityNum")), _f(m.get("volume24hr")), _f(m.get("oneHourPriceChange"))
            end = m.get("endDate")
            if liq is None or vol is None or ch is None or not end or liq < MIN_LIQ or vol < MIN_VOL or abs(ch) < MOVE:
                continue
            if datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp() - now < 24 * 3600:
                continue
            mid = str(m["id"])
            if mid in b["positions"] or len(b["positions"]) >= MAX_OPEN or b["cash"] < STAKE:
                continue
            outcomes = json.loads(m.get("outcomes") or "[]")
            outcome = 0 if ch > 0 else 1
            ba, bb = _f(m.get("bestAsk")), _f(m.get("bestBid"))
            entry = ba if outcome == 0 else (1 - bb if bb is not None else None)
            if not entry or not (0.03 <= entry <= 0.97):
                continue
            label = outcomes[outcome] if len(outcomes) > outcome else ("Oui" if outcome == 0 else "Non")
            b["cash"] -= STAKE
            b["positions"][mid] = {"question": m.get("question", ""), "outcome": outcome, "label": label, "entry": entry,
                                   "shares": STAKE / entry, "usd": STAKE, "opened": now, "move_1h": ch, "last": entry}
            signals += 1
            log_event("PolymarketDesk", "polymarket", "BUY", m.get("question", "")[:60],
                      f"probabilite {ch:+.0%} en 1 h (volume 24 h {vol:,.0f} $) : pari fictif {STAKE:.0f} $ sur '{label}' a {entry:.3f}",
                      result="ouvert", kind="polymarket_move", ttl_s=3600)
        except Exception:
            continue
    open_value = sum(p["shares"] * p.get("last", p["entry"]) for p in b["positions"].values())
    b["equity"] = round(b["cash"] + open_value, 2)
    closed = [t for t in b["trades"] if t.get("pnl") is not None]
    b["stats"] = {"closed": len(closed), "won": sum(1 for t in closed if t["pnl"] > 0),
                  "pnl": round(sum(t["pnl"] for t in closed), 2), "open": len(b["positions"]), "signals_last_pass": signals}
    _save(b)
    return b


_started = False


def start() -> None:
    global _started
    if _started or not int(os.environ.get("PM_DESK_ENABLED", 1)):
        return
    _started = True

    def loop():
        time.sleep(240)
        while True:
            try:
                run()
            except Exception:
                pass
            time.sleep(EVERY_S)
    threading.Thread(target=loop, daemon=True, name="PolymarketDesk").start()
