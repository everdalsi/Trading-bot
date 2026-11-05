"""Ultra-short-term scalping sleeves (user request 2026-11-05, PAPER only), holding 1 to 5 min.

Three sub-ledgers, each with SCALP_SHARE (20 %, reduced to 5 % by the Auditor if it loses) of its
parent's capital, kept apart from the main ledgers (no race with their threads):
  scalp_crypto_copy.json  -> strategy A (1m breakout of the 15-min high/low with volume >= 2x)
  scalp_court_terme.json  -> strategy B (1m RSI < 20 / > 80 + close outside 2.5 sigma Bollinger,
                             only in the direction of the 15m trend)
  scalp_actions_us.json   -> strategy A on 1m stock bars, market hours only (14:30-21:00 UTC)
A and B run side by side on the same capital share, so the Professor compares them.

Symbols: crypto = top 15 Binance USDT pairs by 24 h volume with spread < 0.03 %; stocks = 10 liquid
tickers. Exits: TP +0.35 %, SL -0.25 %, trailing stop 0.15 % from the best price once +0.20 %,
mandatory exit at 5 min. Max 5 open scalps per sleeve, daily stop -3 % of the sleeve.
Costs (the main factor at this horizon): crypto taker 0.10 % per side + slippage = half the spread +
0.02 %; stocks 0 commission + 0.02 % slippage per side. Every trade logs gross AND net PnL.
Loop every 10 s for exits, new entries checked once per closed 1-minute candle.
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
BINANCE = "https://api.binance.com"
PARENT_CASH = {"crypto_copy": 1000.0, "court_terme": 1000.0, "actions_us": 1000.0}
STRATEGY = {"crypto_copy": "A", "court_terme": "B", "actions_us": "A"}
TRAIL_ON, TRAIL, MAX_HOLD_S = 0.0020, 0.0015, 300
# Desktop review 2026-11-05: with 0.10 %/side + slippage, TP 0.35 % / SL 0.25 % needs ~82 % winners to
# break even (a guaranteed loss that teaches nothing). TP 0.50 % / SL 0.30 %, and each sleeve pays its
# own venue's taker fee: crypto copy = Hyperliquid 0.045 %, short term = Binance 0.10 % (the Professor
# compares both cost levels). Overridable: SCALP_TP, SCALP_SL, SCALP_FEE_<PORTFOLIO>.
TP = float(os.environ.get("SCALP_TP", 0.0050))
SL = float(os.environ.get("SCALP_SL", 0.0030))
FEE = {"crypto_copy": float(os.environ.get("SCALP_FEE_CRYPTO_COPY", 0.00045)),
       "court_terme": float(os.environ.get("SCALP_FEE_COURT_TERME", 0.0010))}
# V1 (trading-agents-v3.md, 2026-11-05): a 1-5 min taker scalper has negative expectancy after costs.
# Crypto sleeves now SIMULATE post-only maker orders: entry = limit at our side of the book (bid for a
# LONG, ask for a SHORT); filled only when the market later trades THROUGH it (opposite best price
# strictly beyond our limit: conservative queue model, adverse selection included); unfilled after
# MAKER_TTL_S -> cancelled. TP exit = resting maker limit, filled when the book trades through it.
# SL / trailing / 5-min exit stay taker (fee + slippage). Fill rate measured in led["maker"].
MAKER = bool(int(os.environ.get("SCALP_MAKER", 1)))
MAKER_TTL_S = int(os.environ.get("SCALP_MAKER_TTL_S", 60))
MAKER_FEE = {"crypto_copy": float(os.environ.get("SCALP_MAKER_FEE_CRYPTO_COPY", 0.00015)),   # Hyperliquid maker
             "court_terme": float(os.environ.get("SCALP_MAKER_FEE_COURT_TERME", 0.0002))}    # Binance USDT-M maker
CUT_AFTER = int(os.environ.get("SCALP_CUT_AFTER", 300))  # net < 0 after 300 maker trades -> 5 %, after 600 -> 0 %
MAX_OPEN, DAY_STOP = 8, 0.03  # 2026-11-05: 5 -> 8 so the sleeve's capital works when signals come
CRYPTO_SLIP_MIN, STOCK_SLIP = 0.0002, 0.0002
MAX_SPREAD = 0.0003
STOCKS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "META", "GOOGL", "AMD"]
# stablecoins, tokenised gold and tokenised stocks: not crypto price action
STABLES = {"USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "USDE", "EUR", "TRY", "BRL", "PAXG", "WBTC", "USD1",
           "RLUSD", "XAUT", "U", "SNDKB", "SOXLB", "BFUSD", "XUSD", "AEUR", "EURI", "PYUSD"}
_LOCK = threading.Lock()


def _capital(pf: str) -> float:
    """Capital this sleeve may use now = parent capital x current share (20 %, or 5 % after the Auditor)."""
    return PARENT_CASH[pf] * _share(pf)


def _path(pf: str) -> Path:
    return DATA_DIR / f"scalp_{pf}.json"


def _share(pf: str) -> float:
    try:
        cut = maker_share_cut(pf) if MAKER and pf in MAKER_FEE else None
        if cut is not None:
            return cut
    except Exception:
        pass
    try:
        import professor
        return professor.scalp_share(pf)
    except Exception:
        return 0.20


def load(pf: str) -> dict:
    try:
        return json.loads(_path(pf).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cash = PARENT_CASH[pf] * _share(pf)
        return {"cash": cash, "start": cash, "positions": {}, "trades": [], "day": "", "day_start": cash,
                "halted_day": "", "last_bar": {}}


def save(pf: str, led: dict) -> None:
    with _LOCK:
        tmp = _path(pf).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(led), encoding="utf-8")
        tmp.replace(_path(pf))


# ---------------------------------------------------------------- market data

_universe = {"at": 0.0, "syms": []}


def crypto_universe() -> list[str]:
    if time.time() - _universe["at"] > 3600:
        rows = requests.get(f"{BINANCE}/api/v3/ticker/24hr", timeout=15).json()
        rows = [r for r in rows if r["symbol"].endswith("USDT") and r["symbol"][:-4] not in STABLES]
        rows.sort(key=lambda r: -float(r["quoteVolume"]))
        _universe.update(at=time.time(), syms=[r["symbol"] for r in rows[:15]])
    return _universe["syms"]


def book(symbols: list[str]) -> dict[str, tuple[float, float]]:
    """symbol -> (bid, ask) for crypto."""
    rows = requests.get(f"{BINANCE}/api/v3/ticker/bookTicker", timeout=10).json()
    want = set(symbols)
    return {r["symbol"]: (float(r["bidPrice"]), float(r["askPrice"])) for r in rows if r["symbol"] in want}


def bars(symbol: str, interval: str = "1m", limit: int = 40) -> list[tuple]:
    """Closed bars (open_ms, o, h, l, c, v), oldest first."""
    if symbol.endswith("USDT"):
        k = requests.get(f"{BINANCE}/api/v3/klines", params={"symbol": symbol, "interval": interval, "limit": limit + 1},
                         timeout=10).json()
        return [(x[0], float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])) for x in k[:-1]]
    import alpaca_broker
    return alpaca_broker.get_recent_bars(symbol, interval, 2)[-limit:]


def _rsi(c: list[float], n: int = 14) -> float:
    g = [max(0, b - a) for a, b in zip(c, c[1:])]
    l_ = [max(0, a - b) for a, b in zip(c, c[1:])]
    ag, al = sum(g[:n]) / n, sum(l_[:n]) / n
    for x, y in zip(g[n:], l_[n:]):
        ag, al = (ag * (n - 1) + x) / n, (al * (n - 1) + y) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


# ---------------------------------------------------------------- signals

def signal_a(b: list[tuple]) -> str:
    """1m breakout of the previous 15-minute high (LONG) / low (SHORT) with volume >= 2x average."""
    if len(b) < 17:
        return ""
    prev = b[-16:-1]
    hi, lo = max(x[2] for x in prev), min(x[3] for x in prev)
    vol_avg = sum(x[5] for x in prev) / 15
    last = b[-1]
    if vol_avg <= 0 or last[5] < 2 * vol_avg:
        return ""
    if last[4] > hi:
        return "LONG"
    if last[4] < lo:
        return "SHORT"
    return ""


def signal_b(symbol: str, b: list[tuple]) -> str:
    """Mean reversion: RSI(14) < 20 + close under the 2.5 sigma lower band -> LONG if the 15m trend is
    up; RSI > 80 + close above the upper band -> SHORT if the 15m trend is down."""
    if len(b) < 25:
        return ""
    c = [x[4] for x in b]
    r = _rsi(c)
    w = c[-20:]
    m = sum(w) / 20
    sd = (sum((x - m) ** 2 for x in w) / 20) ** 0.5
    if not (r < 20 and c[-1] < m - 2.5 * sd) and not (r > 80 and c[-1] > m + 2.5 * sd):
        return ""
    c15 = [x[4] for x in bars(symbol, "15m", 22)]
    if len(c15) < 20:
        return ""
    up = c15[-1] > sum(c15[-20:]) / 20
    if r < 20 and up:
        return "LONG"
    if r > 80 and not up:
        return "SHORT"
    return ""


# ---------------------------------------------------------------- execution (simulated)

def _cost_side(pf: str, bid: float, ask: float) -> float:
    if pf == "actions_us":
        return STOCK_SLIP
    mid = (bid + ask) / 2
    return FEE.get(pf, 0.0010) + max(CRYPTO_SLIP_MIN, (ask - bid) / 2 / mid if mid else CRYPTO_SLIP_MIN)


def _close(pf: str, led: dict, key: str, bid: float, ask: float, reason: str, maker_px: float | None = None) -> None:
    p = led["positions"].pop(key)
    px = maker_px if maker_px else (bid if p["side"] == "LONG" else ask)
    move = (px - p["entry"]) / p["entry"] * (1 if p["side"] == "LONG" else -1)
    gross = p["usd"] * move
    cost = p["usd"] * (p["cost_in"] + (MAKER_FEE.get(pf, 0.0002) if maker_px else _cost_side(pf, bid, ask)))
    net = gross - cost
    led["cash"] += p["usd"] + net
    led["trades"].append({"symbol": p["symbol"], "side": p["side"], "strategy": p["strategy"], "trade_type": "SCALP",
                          "entry": p["entry"], "exit": px, "usd": p["usd"], "gross": round(gross, 5),
                          "pnl": round(net, 5), "pnl_pct": round(net / p["usd"] * 100, 4), "reason": reason,
                          "exec": p.get("exec", "taker"), "exit_exec": "maker" if maker_px else "taker",
                          "opened": p["opened"], "closed": time.time(), "hold_s": int(time.time() - p["opened"])})
    led["trades"] = led["trades"][-5000:]
    log_event("Scalper", pf, "CLOSE", p["symbol"], f"{p['strategy']} {p['side']} : {reason}", score=round(move * 100, 3),
              result=round(net, 4), gross=round(gross, 4), costs=round(cost, 4), hold_s=int(time.time() - p["opened"]))


def _open(pf: str, led: dict, symbol: str, side: str, bid: float, ask: float, strategy: str,
          maker_px: float | None = None) -> None:
    usd = min(led["cash"], _capital(pf) / MAX_OPEN)  # current share (the Auditor may have cut it to 5 %)
    if usd < 5:
        return
    px = maker_px if maker_px else (ask if side == "LONG" else bid)
    led["cash"] -= usd
    led["positions"][f"{symbol}:{int(time.time())}"] = {
        "symbol": symbol, "side": side, "entry": px, "usd": usd, "best": px, "opened": time.time(),
        "strategy": strategy, "exec": "maker" if maker_px else "taker",
        "cost_in": MAKER_FEE.get(pf, 0.0002) if maker_px else _cost_side(pf, bid, ask)}
    log_event("Scalper", pf, "BUY" if side == "LONG" else "SELL", symbol, f"strategie {strategy} : {side} 1-5 min",
              result="ouvert", price=px, usd=round(usd, 2))


def _maker_stat(led: dict, k: str) -> None:
    st = led.setdefault("maker", {"placed": 0, "filled": 0, "expired": 0})
    st[k] = st.get(k, 0) + 1


def pending_fills(pf: str, led: dict, quotes: dict[str, tuple[float, float]]) -> None:
    """Resting post-only entry limits: filled only if the opposite best price traded THROUGH the limit."""
    now = time.time()
    for s, o in list(led.get("pending", {}).items()):
        q = quotes.get(s)
        if q and ((o["side"] == "LONG" and q[1] < o["limit"]) or (o["side"] == "SHORT" and q[0] > o["limit"])):
            del led["pending"][s]
            _maker_stat(led, "filled")
            _open(pf, led, s, o["side"], q[0], q[1], o["strategy"], maker_px=o["limit"])
        elif now - o["at"] >= MAKER_TTL_S:
            del led["pending"][s]
            _maker_stat(led, "expired")


def maker_share_cut(pf: str) -> float | None:
    """V1 automatic cut: maker trades net < 0 after CUT_AFTER trades -> 5 %, after 2 x CUT_AFTER -> 0 %."""
    try:  # read the file directly: load() calls _share() on a fresh ledger (no recursion)
        t = [x for x in json.loads(_path(pf).read_text(encoding="utf-8"))["trades"] if x.get("exec") == "maker"]
    except (OSError, ValueError, KeyError):
        return None
    if len(t) < CUT_AFTER or sum(x["pnl"] for x in t) >= 0:
        return None
    return 0.0 if len(t) >= 2 * CUT_AFTER else 0.05


def manage(pf: str, led: dict, quotes: dict[str, tuple[float, float]]) -> None:
    now = time.time()
    for key, p in list(led["positions"].items()):
        q = quotes.get(p["symbol"])
        if not q:
            continue
        bid, ask = q
        px = bid if p["side"] == "LONG" else ask
        move = (px - p["entry"]) / p["entry"] * (1 if p["side"] == "LONG" else -1)
        p["best"] = max(p["best"], px) if p["side"] == "LONG" else min(p["best"], px)
        best_move = (p["best"] - p["entry"]) / p["entry"] * (1 if p["side"] == "LONG" else -1)
        tp_px = p["entry"] * (1 + TP) if p["side"] == "LONG" else p["entry"] * (1 - TP)
        if p.get("exec") == "maker" and ((p["side"] == "LONG" and bid > tp_px) or (p["side"] == "SHORT" and ask < tp_px)):
            _close(pf, led, key, bid, ask, f"TP maker +{TP * 100:.2f} %", maker_px=tp_px)
        elif p.get("exec") != "maker" and move >= TP:
            _close(pf, led, key, bid, ask, f"TP +{TP * 100:.2f} %")
        elif move <= -SL:
            _close(pf, led, key, bid, ask, f"SL -{SL * 100:.2f} %")
        elif best_move >= TRAIL_ON and best_move - move >= TRAIL:
            _close(pf, led, key, bid, ask, f"stop suiveur ({best_move * 100:+.2f} % -> {move * 100:+.2f} %)")
        elif now - p["opened"] >= MAX_HOLD_S:
            _close(pf, led, key, bid, ask, "sortie obligatoire 5 min")


def entries(pf: str, led: dict, symbols: list[str], quotes: dict[str, tuple[float, float]]) -> None:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    eq = led["cash"] + sum(p["usd"] for p in led["positions"].values())
    if led["day"] != day:
        led.update(day=day, day_start=eq)
    if len(led["positions"]) == 0 and eq > _capital(pf) * 1.5:
        return  # share cut by the Auditor: the extra cash stays idle, never traded
    stop_base = min(led["day_start"], _capital(pf))
    if led["halted_day"] == day:
        return
    if eq < led["day_start"] - stop_base * DAY_STOP:
        led["halted_day"] = day
        log_event("Scalper", pf, "HALT", "", f"stop journalier -{DAY_STOP:.0%} du sleeve atteint", result="plus d'entree aujourd'hui")
        return
    held = {p["symbol"] for p in led["positions"].values()} | set(led.get("pending", {}))
    for s in symbols:
        if len(led["positions"]) + len(led.get("pending", {})) >= MAX_OPEN:
            break
        q = quotes.get(s)
        if s in held or not q or q[0] <= 0:
            continue
        bid, ask = q
        if pf != "actions_us" and (ask - bid) / ((ask + bid) / 2) > MAX_SPREAD:
            continue
        b = bars(s)
        if not b or led["last_bar"].get(s) == b[-1][0]:
            continue  # one decision per closed 1-minute candle
        led["last_bar"][s] = b[-1][0]
        side = signal_a(b) if STRATEGY[pf] == "A" else signal_b(s, b)
        if side and MAKER and pf in MAKER_FEE:
            led.setdefault("pending", {})[s] = {"side": side, "limit": bid if side == "LONG" else ask,
                                                "at": time.time(), "strategy": STRATEGY[pf]}
            _maker_stat(led, "placed")
            held.add(s)
        elif side:
            _open(pf, led, s, side, bid, ask, STRATEGY[pf])
            held.add(s)


def _stock_quotes() -> dict[str, tuple[float, float]]:
    """LIMITATION: last 1-minute close as both bid and ask (no live quote in alpaca_broker), so stock
    TP/SL are checked up to 1 min late and filled at the close (optimistic on gaps)."""
    out = {}
    for s in STOCKS:
        try:
            b = bars(s, "1m", 2)
            if b:
                out[s] = (b[-1][4], b[-1][4])  # last 1m close (bid = ask): slippage is in the cost model
        except Exception:
            continue
    return out


def _market_open_utc() -> bool:
    n = datetime.now(timezone.utc)
    return n.weekday() < 5 and (14, 30) <= (n.hour, n.minute) < (21, 0)


_last_minute = {"m": -1}


def tick() -> None:
    minute = int(time.time() // 60)
    new_minute = minute != _last_minute["m"]  # entries once per closed 1-minute candle, exits every tick
    _last_minute["m"] = minute
    syms = crypto_universe()
    quotes = book(syms + [s for pf in ("crypto_copy", "court_terme") for led_ in (load(pf),)
                          for s in [p["symbol"] for p in led_["positions"].values()] + list(led_.get("pending", {}))])
    for pf in ("crypto_copy", "court_terme"):
        led = load(pf)
        pending_fills(pf, led, quotes)
        manage(pf, led, quotes)
        if new_minute:
            entries(pf, led, syms, quotes)
        save(pf, led)
    led = load("actions_us")
    if led["positions"] or _market_open_utc():
        q = _stock_quotes()
        manage("actions_us", led, q)
        if _market_open_utc() and new_minute:
            entries("actions_us", led, STOCKS, q)
        save("actions_us", led)


def summary(pf: str) -> dict:
    led = load(pf)
    t = led["trades"]
    return {"cash": round(led["cash"], 2), "start": led["start"], "open": len(led["positions"]), "trades": len(t),
            "gross": round(sum(x["gross"] for x in t), 2), "net": round(sum(x["pnl"] for x in t), 2),
            "win_rate": round(sum(x["pnl"] > 0 for x in t) / len(t) * 100, 1) if t else 0.0,
            "maker": led.get("maker"), "pending": len(led.get("pending", {}))}


_started = False


def start(every_s: int = 10) -> None:
    global _started
    if _started or not int(os.environ.get("SCALPER_ENABLED", 1)):
        return
    _started = True

    def loop():
        time.sleep(60)
        while True:
            try:
                tick()
            except Exception as e:
                log_event("Scalper", "systeme", "INFO", "", f"erreur interne {type(e).__name__}: {str(e)[:150]}")
                time.sleep(30)
            time.sleep(every_s)

    threading.Thread(target=loop, daemon=True, name="scalper").start()
