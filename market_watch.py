"""MarketWatch: continuous order-book and manipulation watch on the 50-market universe (user 06/11: "construis tout").

Wakes up agents that only ran at entry time (they were idle on the trading floor):
- OrderFlow (analyste du carnet d'ordres), every BOOK_EVERY_S: per market trade-flow imbalance over 60 s (TFI),
  top-10 book imbalance (OBI), best bid/ask and spread in bps (one bookTicker call for all markets)
  -> DATA_DIR/orderbook.json. The spreads feed the realistic simulator (fills priced through the spread).
- Manipulation (detecteur), every MANIP_EVERY_S: manipulation.check() on every market -> DATA_DIR/manipulation.json;
  a market turning "suspect" is logged once per hour (SIGNAL), read by the desks and the dashboard.
Public Binance REST only (no key). Never raises. Measurement: nothing here opens or closes a position.
"""
import json
import os
import threading
import time
from pathlib import Path

import requests

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
BOOK = DATA_DIR / "orderbook.json"
MANIP = DATA_DIR / "manipulation.json"
BOOK_EVERY_S = int(os.environ.get("MARKET_WATCH_BOOK_S", 120))
MANIP_EVERY_S = int(os.environ.get("MARKET_WATCH_MANIP_S", 900))
_flagged: dict[str, float] = {}


def _save(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _universe() -> list[str]:
    try:
        import market_desk
        return market_desk.universe()
    except Exception:
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def run_book() -> dict:
    syms = _universe()
    try:
        bt = {r["symbol"]: r for r in requests.get("https://api.binance.com/api/v3/ticker/bookTicker", timeout=10).json()}
    except Exception:
        bt = {}
    import orderflow
    out = {}
    for s in syms:
        row = {}
        b = bt.get(s)
        if b:
            bid, ask = float(b["bidPrice"]), float(b["askPrice"])
            if bid > 0 and ask > 0:
                row.update(bid=bid, ask=ask, spread_bps=round((ask - bid) / ((ask + bid) / 2) * 1e4, 2))
        try:
            f = orderflow.flow(s)
            row.update({k: f.get(k) for k in ("tfi", "obi", "n_trades", "buy_usd", "sell_usd")})
        except Exception:
            pass
        if row:
            out[s] = row
    # spreads of EVERY USDT market (same single bookTicker call): the simulator prices any copied coin
    spreads = {}
    for sym, b in bt.items():
        if sym.endswith("USDT"):
            try:
                bid, ask = float(b["bidPrice"]), float(b["askPrice"])
                if bid > 0 and ask > 0:
                    spreads[sym] = round((ask - bid) / ((ask + bid) / 2) * 1e4, 2)
            except (KeyError, ValueError):
                continue
    data = {"at": time.time(), "markets": out, "n": len(out), "spreads_bps": spreads}
    _save(BOOK, data)
    _fees(data)
    return data


def _fees(book: dict) -> None:
    """Fee manager (06/11): real fee schedule per venue + measured costs, for the CostAnalyst and the dashboard."""
    try:
        import agency
        import execution_controller
        rt, src = agency.round_trip_cost("crypto_copy")
        meas = execution_controller.summary()
    except Exception:
        rt, src, meas = None, "indisponible", {}
    sp = sorted(v for v in (book.get("spreads_bps") or {}).values() if v is not None)
    _save(DATA_DIR / "fees.json", {
        "at": time.time(),
        "schedule": {"hyperliquid_perps": {"taker": 0.00045, "maker": 0.00015},
                     "binance_spot": {"taker": 0.001, "maker": 0.001}},
        "crypto_copy_round_trip": rt, "round_trip_source": src,
        "spread_median_bps_all_usdt": sp[len(sp) // 2] if sp else None,
        "execution_measured": (meas or {}).get("portfolios")})


def run_manip() -> dict:
    import manipulation
    from agent_events import log_event
    out = {}
    for s in _universe():
        try:
            m = manipulation.check(s)
        except Exception:
            continue
        out[s] = {k: m.get(k) for k in ("verdict", "score", "flags")}
        if m.get("verdict") == "suspect" and time.time() - _flagged.get(s, 0) > 3600:
            _flagged[s] = time.time()
            log_event("Manipulation", "systeme", "SIGNAL", s,
                      f"marche suspect (score {m.get('score')}) : " + ", ".join(f"{k} {v}" for k, v in (m.get("flags") or {}).items() if v),
                      result="suspect", kind="manipulation", confidence=m.get("score"), ttl_s=3600)
    data = {"at": time.time(), "markets": out, "suspects": sorted(s for s, v in out.items() if v.get("verdict") == "suspect")}
    _save(MANIP, data)
    return data


def spread_bps(symbol: str) -> float | None:
    """Latest measured spread of a market (None when unknown: the caller keeps its own cost model)."""
    try:
        d = json.loads(BOOK.read_text(encoding="utf-8"))
        if time.time() - d["at"] > 3 * BOOK_EVERY_S:
            return None
        return (d["markets"].get(symbol) or {}).get("spread_bps") or (d.get("spreads_bps") or {}).get(symbol)
    except Exception:
        return None


_started = False


def start() -> None:
    global _started
    if _started or not int(os.environ.get("MARKET_WATCH_ENABLED", 1)):
        return
    _started = True

    def book_loop():
        time.sleep(60)
        while True:
            try:
                run_book()
            except Exception:
                pass
            time.sleep(BOOK_EVERY_S)

    def manip_loop():
        time.sleep(180)
        while True:
            try:
                run_manip()
            except Exception:
                pass
            time.sleep(MANIP_EVERY_S)
    threading.Thread(target=book_loop, daemon=True, name="OrderFlowWatch").start()
    threading.Thread(target=manip_loop, daemon=True, name="ManipulationWatch").start()
