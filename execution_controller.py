"""ExecutionController (B6 of ~/.claude/plans/trading-agents-v2.md). MEASUREMENT MODE.

Paper fills are simulated with flat cost models (HL copy: fill at last price + fee at close; court terme:
0.10 % fee + 0.20 % slippage per side; scalper: half spread + 0.02 %). This agent checks each new
simulated crypto entry against the REAL Binance order book at that moment:
  realistic fill = VWAP of walking the book (asks for a buy / long, bids for a sell / short) for our size,
  book_slip = realistic fill vs mid, model_slip = what the simulator assumed (passed by the caller),
  impossible = the top 100 levels cannot absorb our size.
US stocks: the Alpaca paper fill vs the reference close at decision time (record_stock).
Records -> DATA_DIR/execution_quality.jsonl; summary per portfolio -> execution_quality.json.
ALERT event when book_slip > 2 x model_slip (the paper costs are too optimistic for that market).
Never blocks or changes a trade (runs in its own short-lived thread): step 2 = correct the costs once
measured.
"""
import json
import os
import threading
import time
from pathlib import Path

import requests

from agent_events import log_event

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
LOG = DATA_DIR / "execution_quality.jsonl"
OUT = DATA_DIR / "execution_quality.json"
BINANCE = "https://api.binance.com"
_LOCK = threading.Lock()
_alerted: dict[tuple[str, str], str] = {}  # (portfolio, symbol) -> day: one ALERT per symbol per day


def book_fill(symbol: str, side: str, usd: float) -> dict | None:
    """Walk the Binance spot book: VWAP for `usd` notional, mid, and whether depth was enough."""
    pair = symbol if symbol.endswith("USDT") else symbol + "USDT"
    r = requests.get(f"{BINANCE}/api/v3/depth", params={"symbol": pair, "limit": 100}, timeout=10)
    if r.status_code != 200:
        return None
    d = r.json()
    bids, asks = d.get("bids") or [], d.get("asks") or []
    if not bids or not asks:
        return None
    mid = (float(bids[0][0]) + float(asks[0][0])) / 2
    levels = asks if side in ("LONG", "BUY") else bids
    left, qty, cost = usd, 0.0, 0.0
    for px, q in levels:
        px, q = float(px), float(q)
        take = min(left, px * q)
        qty += take / px
        cost += take
        left -= take
        if left <= 1e-9:
            break
    if qty <= 0:
        return None
    vwap = cost / qty
    slip = (vwap - mid) / mid if side in ("LONG", "BUY") else (mid - vwap) / mid
    return {"mid": mid, "vwap": vwap, "book_slip": slip, "spread": (float(asks[0][0]) - float(bids[0][0])) / mid,
            "impossible": left > 1e-6}


def _append(rec: dict) -> None:
    with _LOCK:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


def _check(portfolio: str, symbol: str, side: str, usd: float, sim_price: float, model_slip: float) -> None:
    try:
        b = book_fill(symbol, side, usd)
    except Exception:
        return
    if not b:
        return
    rec = {"at": time.time(), "pf": portfolio, "symbol": symbol, "side": side, "usd": round(usd, 2),
           "sim_price": sim_price, "model_slip": model_slip, **{k: (round(v, 6) if isinstance(v, float) else v)
                                                                for k, v in b.items()}}
    # what the simulator's entry price implies vs the book's mid, sign-adjusted (positive = we paid more)
    rec["sim_vs_mid"] = round(((sim_price - b["mid"]) / b["mid"]) * (1 if side in ("LONG", "BUY") else -1), 6)
    _append(rec)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if (b["impossible"] or b["book_slip"] > 2 * max(model_slip, 0.0001)) and _alerted.get((portfolio, symbol)) != day:
        _alerted[(portfolio, symbol)] = day
        log_event("ExecutionController", portfolio, "ALERT", symbol.replace("USDT", ""),
                  ("profondeur insuffisante pour " if b["impossible"] else f"glissement reel {b['book_slip']:.3%} > 2 x modele {model_slip:.3%} pour ")
                  + f"{usd:.0f} $", score=round(b["book_slip"], 6), result="couts papier trop optimistes")


def record(portfolio: str, symbol: str, side: str, usd: float, sim_price: float, model_slip: float) -> None:
    """Non-blocking: check one simulated crypto entry against the real book."""
    if not int(os.environ.get("EXECUTION_CONTROLLER_ENABLED", 1)):
        return
    threading.Thread(target=_check, args=(portfolio, symbol, side, usd, sim_price, model_slip), daemon=True).start()


def record_stock(symbol: str, side: str, usd: float, fill: float | None, ref: float | None) -> None:
    if not fill or not ref:
        return
    slip = (fill - ref) / ref * (1 if side in ("LONG", "BUY") else -1)
    _append({"at": time.time(), "pf": "actions_us", "symbol": symbol, "side": side, "usd": round(usd, 2),
             "sim_price": fill, "ref": ref, "book_slip": round(slip, 6), "model_slip": 0.0, "impossible": False})


def summary(days: float = 7) -> dict:
    try:
        rows = [json.loads(x) for x in LOG.read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        rows = []
    rows = [r for r in rows if time.time() - r["at"] <= days * 86400]
    out = {}
    for pf in sorted({r["pf"] for r in rows}):
        rs = [r for r in rows if r["pf"] == pf]
        sl = sorted(r["book_slip"] for r in rs)
        out[pf] = {"n": len(rs), "book_slip_median": sl[len(sl) // 2], "book_slip_p90": sl[int(0.9 * (len(sl) - 1))],
                   "model_slip_median": sorted(r["model_slip"] for r in rs)[len(rs) // 2],
                   "impossible": sum(1 for r in rs if r.get("impossible")),
                   "too_optimistic": sum(1 for r in rs if r["book_slip"] > 2 * max(r["model_slip"], 0.0001))}
    data = {"at": time.time(), "days": days, "portfolios": out}
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(OUT)
    return data
