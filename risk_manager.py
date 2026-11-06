"""RiskManager (B1 of ~/.claude/plans/trading-agents-v2.md, with V4 of trading-agents-v3.md).

One place for position sizing and the portfolio-level risk picture of the 4 paper portfolios.
The existing guards (DrawdownGuard, RiskLimits, TradeManager, Scalper day stop, short sleeve _risk,
SeniorExpert) stay where they are and are READ here as inputs; nothing is removed.

Sizing (V4: volatility targeting):
- vol_mult(symbol): VT_REF_ATR / daily ATR %, clamped to [0.5, 1.5]. A coin twice as volatile as the
  reference gets half the size, so each position carries about the same daily risk.
- risk_parity(symbols, budget): inverse-ATR weights scaled so the WHOLE budget is used (the user's
  ~100 % invested rule), each weight capped at 2x the equal weight.
- kelly_cap(trades): capped 0.25 Kelly from >= 100 closed trades of the same strategy, else None.
  Used only as an upper bound on a position's share, never to size up past the 5 % rule.

Monitoring: every 5 min, risk_manager.json = per portfolio {equity, invested, invested_pct,
positions, day_pnl, drawdown_from_peak} + violations (exposure > 100 %, side+category > 30 %);
a VIOLATION event is logged once per new violation.
"""
import json
import os
import threading
import time
from pathlib import Path

from agent_events import log_event

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "risk_manager.json"
VT_REF_ATR = float(os.environ.get("RISK_VT_REF_ATR", 0.04))  # reference daily ATR (4 % = a typical large-cap coin)
VT_MIN, VT_MAX = 0.5, 1.5
KELLY_MIN_TRADES, KELLY_FRACTION = 100, 0.25
_atr_cache: dict[str, tuple[float, float]] = {}
category_fn = None  # set by bot.py at startup (bot._hl_category); never import bot from here


def _pair(symbol: str) -> str:
    s = symbol.upper()
    if s.startswith("K") and s[1:].isupper() and len(s) > 4:  # HL 1000x tickers (kPEPE -> PEPE)
        s = s[1:]
    return s if s.endswith("USDT") or not s.isalpha() or len(s) > 10 else s + "USDT"


def atr_pct(symbol: str) -> float | None:
    """Daily ATR(14) / price, cached 1 h. None when there is no Binance daily data."""
    pair = _pair(symbol)
    hit = _atr_cache.get(pair)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        import chart_analyst
        c = chart_analyst.candles(pair, "1d", 30)
        if len(c) < 15:
            return None
        v = chart_analyst.atr_series(c)[-1] / c[-1][4]
    except Exception:
        return None
    _atr_cache[pair] = (time.time(), v)
    return v


def vol_mult(symbol: str) -> float:
    a = atr_pct(symbol)
    if not a or a <= 0:
        return 1.0
    return max(VT_MIN, min(VT_MAX, VT_REF_ATR / a))


def risk_parity(symbols: list[str], budget: float) -> dict[str, float]:
    """USD per symbol: inverse-ATR weights summing to the whole budget, each capped at 2x equal weight."""
    if not symbols:
        return {}
    inv = {}
    for s in symbols:
        a = atr_pct(s)
        inv[s] = 1.0 / a if a and a > 0 else None
    known = [v for v in inv.values() if v]
    med = sorted(known)[len(known) // 2] if known else 1.0
    inv = {s: (v or med) for s, v in inv.items()}
    cap = 2.0 / len(symbols)
    w = {s: v / sum(inv.values()) for s, v in inv.items()}
    for _ in range(5):  # cap and redistribute the excess
        over = {s: x for s, x in w.items() if x > cap}
        if not over:
            break
        excess = sum(x - cap for x in over.values())
        free = {s: x for s, x in w.items() if x < cap}
        for s in over:
            w[s] = cap
        tot = sum(free.values()) or 1.0
        for s, x in free.items():
            w[s] = x + excess * x / tot
    return {s: budget * x for s, x in w.items()}


def kelly_cap(trades: list[dict], key: str = "pnl_pct") -> float | None:
    """0.25 x Kelly fraction from >= 100 closed trades (win rate and payoff ratio), clamped [0, 1]."""
    r = [float(t[key]) for t in trades if t.get(key) is not None]
    if len(r) < KELLY_MIN_TRADES:
        return None
    wins, losses = [x for x in r if x > 0], [-x for x in r if x < 0]
    if not wins or not losses:
        return None
    p, b = len(wins) / len(r), (sum(wins) / len(wins)) / (sum(losses) / len(losses))
    return max(0.0, min(1.0, KELLY_FRACTION * (p - (1 - p) / b)))


# ---------------------------------------------------------------- monitoring

def _json(name: str) -> dict:
    try:
        return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def snapshot() -> dict:
    out, violations = {}, []
    hl = _json("hl_copy_ledger.json")
    if hl:
        pos = list(hl.get("positions", {}).values())
        inv = sum(float(p.get("amount_usd", 0)) for p in pos)
        eq = float(hl.get("cash", 0)) + inv
        out["crypto_copy"] = {"equity": round(eq, 2), "invested": round(inv, 2), "positions": len(pos)}
        buckets = {}
        for p in pos:
            if p.get("strategy") in ("HL_HEDGE", "HL_PAIR"):  # pair legs are matched long/short
                continue  # hedge legs reduce the net exposure: outside the 30 % side+category rule by design
            try:
                cat = category_fn(p["symbol"]) if category_fn else "?"
            except Exception:
                cat = "?"
            k = f"{p.get('side')} {cat}"
            buckets[k] = buckets.get(k, 0) + float(p.get("amount_usd", 0))
        out["crypto_copy"]["side_category"] = {k: round(v / eq, 3) for k, v in buckets.items()} if eq else {}
        violations += [f"crypto_copy {k} {v:.0%} > 30 %" for k, v in out["crypto_copy"]["side_category"].items()
                       if v > 0.30 + 1e-6]
    al = _json("alpaca_stocks_ledger.json")
    if al:
        inv = sum(float(p.get("cost", 0)) for p in al.get("positions", {}).values())
        eq = float(al.get("virtual_cash", 1000)) + inv
        out["actions_us"] = {"equity": round(eq, 2), "invested": round(inv, 2), "positions": len(al.get("positions", {}))}
    ct = _json("meme_sim.json")
    if ct:
        pos = list(ct.get("positions", {}).values())
        inv = sum(float(p.get("usd", 0)) for p in pos)
        out["court_terme"] = {"equity": round(float(ct.get("cash", 0)) + inv, 2), "invested": round(inv, 2),
                              "positions": len(pos), "side": ct.get("a_side")}
    for pf in ("crypto_copy", "court_terme", "actions_us"):
        sc = _json(f"scalp_{pf}.json")
        if sc:
            out.setdefault(pf, {})["scalp_open"] = len(sc.get("positions", {}))
    for pf, d in out.items():
        if d.get("equity"):
            d["invested_pct"] = round(d["invested"] / d["equity"], 3)
            if d["invested_pct"] > 1.0 + 1e-6:
                violations.append(f"{pf} expose a {d['invested_pct']:.0%} > 100 %")
        peak = max(d.get("equity", 0), (_json("risk_manager.json").get("portfolios", {}).get(pf, {}) or {}).get("peak", 0))
        d["peak"] = round(peak, 2)
        d["drawdown_from_peak"] = round(1 - d["equity"] / peak, 4) if peak and d.get("equity") else 0.0
    return {"at": time.time(), "portfolios": out, "violations": violations}


def run() -> dict:
    old = set(_json("risk_manager.json").get("violations", []))
    snap = snapshot()
    for v in snap["violations"]:
        if v not in old:
            log_event("RiskManager", "systeme", "VIOLATION", "", v, result="a corriger")
    try:  # ExecutionController summary refreshed with the risk snapshot (every 5 min)
        import execution_controller
        execution_controller.summary()
    except Exception:
        pass
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap), encoding="utf-8")
    tmp.replace(OUT)
    return snap


def read() -> dict:
    return _json("risk_manager.json")


_started = False


def start(every_s: int = 300) -> None:
    global _started
    if _started or not int(os.environ.get("RISK_MANAGER_ENABLED", 1)):
        return
    _started = True

    def loop():
        time.sleep(90)
        while True:
            try:
                run()
            except Exception as e:
                log_event("RiskManager", "systeme", "INFO", "", f"erreur {type(e).__name__}: {str(e)[:150]}")
            time.sleep(every_s)

    threading.Thread(target=loop, daemon=True, name="risk_manager").start()
