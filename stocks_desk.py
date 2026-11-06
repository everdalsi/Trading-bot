"""StocksDesk: US stocks paper desk, recreated with a fresh book (user 06/11: "recree un desk actions, nouvelles
regles, mesure comme les autres"; Alpaca removed the same morning).

MEASUREMENT BOOK, outside the 2,000 $ capital, free public daily prices (Yahoo chart API, no key, no account).
Rule (trend following, long only, evaluated once per US trading day after the close):
- enter LONG when close > MA50, MA20 > MA50 and RSI14 < 70 (not overbought), STAKE $ per position, max MAX_OPEN;
- exit when close < MA20, or -6 % from entry (catastrophe stop), at the daily close.
Universe: liquid ETFs + mega caps. Costs: 0.05 % per side (spread estimate for these names, stated, not measured).
-> DATA_DIR/stocks_desk.json, events StocksDesk BUY / CLOSE / INFO. Never raises.
"""
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
BOOK = DATA_DIR / "stocks_desk.json"
UNIVERSE = ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL",
            "META", "TSLA", "AVGO", "JPM", "V", "COST", "LLY"]
STAKE, MAX_OPEN, STOP, COST = 50.0, 10, -0.06, 0.0005
START_CASH = 500.0


def _load() -> dict:
    try:
        return json.loads(BOOK.read_text(encoding="utf-8"))
    except Exception:
        return {"start": START_CASH, "cash": START_CASH, "positions": {}, "trades": [], "last_day": "",
                "mode": "carnet de mesure, hors capital", "cost_note": "0.05 %/cote estime (spread), non mesure"}


def _save(b: dict) -> None:
    b["at"] = time.time()
    tmp = BOOK.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    tmp.replace(BOOK)


def closes(sym: str) -> tuple[list[float], str] | None:
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params={"interval": "1d", "range": "6mo"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15).json()["chart"]["result"][0]
        c = [x for x in r["indicators"]["quote"][0]["close"]]
        ts = r["timestamp"]
        pairs = [(t, x) for t, x in zip(ts, c) if x is not None]
        if len(pairs) < 60:
            return None
        last_day = datetime.fromtimestamp(pairs[-1][0], timezone.utc).strftime("%Y-%m-%d")
        return [x for _, x in pairs], last_day
    except Exception:
        return None


def _rsi(c: list[float], n: int = 14) -> float:
    g = l = 0.0
    for a, b in zip(c[-n - 1:-1], c[-n:]):
        d = b - a
        g, l = (g + d, l) if d > 0 else (g, l - d)
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def run() -> dict:
    from agent_events import log_event
    b = _load()
    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() >= 5 or (now_utc.hour, now_utc.minute) < (21, 15):  # after the US close (21:00 UTC)
        return b
    day = now_utc.strftime("%Y-%m-%d")
    if b.get("last_day") == day:
        return b
    data = {s: closes(s) for s in UNIVERSE}
    if not any(data.values()):
        log_event("StocksDesk", "actions", "INFO", "", "prix indisponibles (Yahoo) : rien decide aujourd'hui")
        return b
    for s, p in list(b["positions"].items()):  # exits first
        d = data.get(s)
        if not d:
            continue
        c = d[0]
        px, ma20 = c[-1], sum(c[-20:]) / 20
        ret = px / p["entry"] - 1
        why = "stop -6 %" if ret <= STOP else "cloture sous la MM20" if px < ma20 else ""
        if why:
            value = p["shares"] * px * (1 - COST)
            pnl = value - p["usd"]
            b["cash"] += value
            b["trades"].append({**p, "exit": px, "pnl": round(pnl, 4), "closed": time.time(), "reason": why})
            del b["positions"][s]
            log_event("StocksDesk", "actions", "CLOSE", s, f"{why} ({ret:+.1%})", result=round(pnl, 4), price=px)
        else:
            p["last"] = px
    for s, d in data.items():  # entries
        if not d or s in b["positions"] or len(b["positions"]) >= MAX_OPEN or b["cash"] < STAKE:
            continue
        c = d[0]
        px, ma20, ma50, rsi = c[-1], sum(c[-20:]) / 20, sum(c[-50:]) / 50, _rsi(c)
        if px > ma50 and ma20 > ma50 and rsi < 70:
            fill = px * (1 + COST)
            b["cash"] -= STAKE
            b["positions"][s] = {"symbol": s, "entry": fill, "shares": STAKE / fill, "usd": STAKE, "opened": time.time(),
                                 "last": px, "why": f"tendance haussiere (MM20 > MM50, RSI {rsi:.0f})"}
            log_event("StocksDesk", "actions", "BUY", s, f"tendance haussiere : cloture {px:.2f} > MM50 {ma50:.2f}, RSI {rsi:.0f}",
                      result="ouvert", price=px, usd=STAKE)
    b["last_day"] = day
    value = sum(p["shares"] * p.get("last", p["entry"]) for p in b["positions"].values())
    b["equity"] = round(b["cash"] + value, 2)
    closed = [t for t in b["trades"] if t.get("pnl") is not None]
    b["stats"] = {"closed": len(closed), "won": sum(1 for t in closed if t["pnl"] > 0),
                  "pnl": round(sum(t["pnl"] for t in closed), 2), "open": len(b["positions"])}
    log_event("StocksDesk", "actions", "INFO", "", f"seance {day} : {len(b['positions'])} positions, valeur {b['equity']:.2f} $",
              result="cycle")
    _save(b)
    return b


_started = False


def start() -> None:
    global _started
    if _started or not int(os.environ.get("STOCKS_DESK_ENABLED", 1)):
        return
    _started = True

    def loop():
        time.sleep(300)
        while True:
            try:
                run()
            except Exception:
                pass
            time.sleep(1800)
    threading.Thread(target=loop, daemon=True, name="StocksDesk").start()
