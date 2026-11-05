"""SPY brake for the US stocks sleeve (Desktop decision 2026-11-05, after the tech sell-off: the sleeve had
no market brake, only R3 -6 % per position and the DrawdownGuard buy pause).

ON  when SPY < its 20-day average AND SPY <= -1.5 % on the day (both, so normal noise does not trigger it).
OFF when SPY is back above its 20-day average, or on a new trading day if the previous day closed > -0.5 %.
While ON: (a) no new longs and no fill top-ups, (b) R3 on stocks tightened to -4 % (instead of -6 %),
(c) it never closes anything by itself. Takes precedence over the ~100 % fill.

Measurement (kept only if it saves money over >= 10 activations):
- every blocked buy and every R3 close between -4 % and -6 % -> DATA_DIR/spy_brake_log.jsonl;
- evaluate() fills, one trading day later, what the blocked buy would have made and what the position
  closed early did afterwards (positive = the brake saved money). The Professor writes the totals.
Flag ALPACA_SPY_BRAKE (1 = on, default).
"""
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from agent_events import log_event

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
STATE = DATA_DIR / "spy_brake.json"
LOG = DATA_DIR / "spy_brake_log.jsonl"
ON_DAY_PCT = -1.5
OFF_DAY_PCT = -0.5
TIGHT_R3_PCT = -4.0
CACHE_S = 300
_LOCK = threading.Lock()
_cache = {"at": 0.0, "st": None}


def enabled() -> bool:
    return bool(int(os.environ.get("ALPACA_SPY_BRAKE", 1)))


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"on": False, "activations": 0, "blocks": 0}


def _save(st: dict) -> None:
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st), encoding="utf-8")
    tmp.replace(STATE)


def _market():
    """(live price, last completed daily closes oldest first, today's date) from Alpaca bars."""
    import alpaca_broker
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    days = alpaca_broker.get_recent_bars("SPY", "1d", 45)
    closes = [b[4] for b in days if datetime.fromtimestamp(b[0] / 1000, timezone.utc).strftime("%Y-%m-%d") < today]
    live = alpaca_broker.get_recent_bars("SPY", "15m", 2)
    return (live[-1][4] if live else None), closes, today


def decide(st: dict, live: float, closes: list[float], today: str) -> dict:
    """Pure state transition (tested without network)."""
    ma20 = sum(closes[-20:]) / 20
    day = (live / closes[-1] - 1) * 100
    st.update(spy=round(live, 2), ma20=round(ma20, 2), day_pct=round(day, 2), checked=time.time())
    if not st.get("on"):
        if live < ma20 and day <= ON_DAY_PCT:
            st.update(on=True, since=time.time(), day=today, activations=st.get("activations", 0) + 1,
                      why=f"SPY {live:.2f} < MM20 {ma20:.2f} et {day:+.2f} % sur la journee")
            st["changed"] = "on"
        return st
    if live > ma20:
        st.update(on=False, why=f"SPY {live:.2f} repasse au-dessus de la MM20 {ma20:.2f}")
        st["changed"] = "off"
    elif st.get("day") != today:
        prev_day = (closes[-1] / closes[-2] - 1) * 100
        if prev_day > OFF_DAY_PCT:
            st.update(on=False, why=f"nouvelle seance, veille cloturee {prev_day:+.2f} % (> {OFF_DAY_PCT} %)")
            st["changed"] = "off"
        else:
            st["day"] = today
    return st


def state() -> dict:
    """Current brake state, refreshed at most every 5 min. Fail-open: any data error keeps the last state."""
    if not enabled():
        return {"on": False}
    if _cache["st"] is not None and time.time() - _cache["at"] < CACHE_S:
        return _cache["st"]
    with _LOCK:
        st = _load()
        try:
            live, closes, today = _market()
            if live and len(closes) >= 21:
                st = decide(st, live, closes, today)
        except Exception:
            pass
        ch = st.pop("changed", None)
        if ch:
            log_event("RegimeAgent", "actions_us", "SIGNAL", "SPY", f"spy_brake {ch} : {st.get('why')}",
                      result=f"spy_brake {ch}", activations=st.get("activations"), blocks=st.get("blocks", 0))
        _save(st)
        _cache.update(at=time.time(), st=st)
    return st


def _append(rec: dict) -> None:
    with _LOCK:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


def record_block(symbol: str, price: float | None, usd: float, kind: str) -> None:
    """A buy the brake stopped (kind 'new' or 'topup'). Counted once per symbol per kind per day."""
    st = _cache["st"] or _load()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"{day}:{kind}:{symbol}"
    seen = st.setdefault("seen", [])
    if key in seen:
        return
    if not price:  # top-ups: the live price at the moment of the block, fetched once per symbol per day
        import alpaca_broker
        bars = alpaca_broker.get_recent_bars(symbol, "15m", 2)
        price = bars[-1][4] if bars else None
    if not price:
        return
    seen.append(key)
    st["seen"] = seen[-200:]
    st["blocks"] = st.get("blocks", 0) + 1
    with _LOCK:
        _save(st)
    _append({"at": time.time(), "type": "block", "kind": kind, "symbol": symbol, "price": price, "usd": round(usd, 2),
             "activation": st.get("activations")})
    log_event("RegimeAgent", "actions_us", "VETO", symbol, f"spy_brake : achat {kind} bloque (SPY {st.get('day_pct')} %)",
              result="achat bloque", blocks=st["blocks"])


def record_tight_close(symbol: str, entry: float, price: float, usd: float) -> None:
    """R3 closed at the tightened -4 % where the normal -6 % would have kept the position."""
    st = _cache["st"] or _load()
    _append({"at": time.time(), "type": "tight_close", "symbol": symbol, "entry": entry, "price": price,
             "usd": round(usd, 2), "activation": st.get("activations")})


def evaluate(price_fn, min_age_s: float = 86400) -> dict:
    """Fill the counterfactual of every row older than a day; returns totals. price_fn(symbol) -> price.
    saved_usd > 0 = the brake saved money (the blocked buy would have lost / the stock kept falling)."""
    try:
        rows = [json.loads(x) for x in LOG.read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        return {"n": 0, "saved_usd": 0.0, "activations": _load().get("activations", 0)}
    changed = False
    for r in rows:
        if "saved_usd" in r or time.time() - r["at"] < min_age_s:
            continue
        try:
            px = price_fn(r["symbol"])
        except Exception:
            px = None
        if not px:
            continue
        move = px / r["price"] - 1  # what the stock did after the block / the early close
        r["later_price"], r["saved_usd"] = px, round(-move * r["usd"], 2)
        changed = True
    if changed:
        with _LOCK:
            tmp = LOG.with_suffix(".jsonl.tmp")
            tmp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            tmp.replace(LOG)
    done = [r for r in rows if "saved_usd" in r]
    return {"n": len(done), "saved_usd": round(sum(r["saved_usd"] for r in done), 2),
            "blocks": sum(1 for r in done if r["type"] == "block"),
            "tight_closes": sum(1 for r in done if r["type"] == "tight_close"),
            "activations": _load().get("activations", 0)}
