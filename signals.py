"""Signal bus + trade circuits + coordinator (desk spec 06/11, step 1: message format & coordination).

Built on agent_events.jsonl (every message is logged there, same file the dashboard reads):
- post(sender, market, kind, confidence, ttl_s, ...) = a signal with a validity; fresh() only returns signals
  that have not expired (an expired signal is never used).
- trade circuits: begin(portfolio, symbol) gives a trade_id kept in a thread-local, so every log_event of that
  thread (ChartAnalyst veto, SeniorExpert, RiskLimits, Executor...) carries it without changing any signature;
  end() closes it. A circuit = signal -> data quality -> security -> devil's advocate -> risk -> decision ->
  execution -> accounting -> Professor.
- coordinator (thread, 60 s): circuits with no step for too long are logged as stuck ("nothing waits in the
  void"), expired signals are counted, DATA_DIR/coordinator.json is written for the dashboard.
Never raises into trading code.
"""
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "coordinator.json"
STUCK_S = {"micro": 120, "default": 900}   # a circuit with no new step for this long is stuck
TERMINAL = {"VETO", "SKIP", "HALT"}         # a circuit also ends on an Executor BUY/SELL/CLOSE or end()
_LOCK = threading.Lock()
_local = threading.local()
_signals: dict[str, dict] = {}              # msg_id -> record (only messages with expires_at)
_circuits: dict[str, dict] = {}             # trade_id -> {portfolio, symbol, opened, last, steps, done, outcome}
_stats = {"signals_posted": 0, "signals_expired_unused": 0, "circuits_opened": 0, "circuits_stuck": 0}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------------ trade circuits
def begin(portfolio: str, symbol: str, origin: str = "") -> str:
    """Open a circuit for one candidate entry in this thread; returns its trade_id."""
    tid = f"{portfolio[:2]}-{symbol.replace('USDT', '')}-{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:4]}"
    prev = current()
    _local.trade_id = tid
    with _LOCK:
        p = _circuits.get(prev) if prev else None
        if p and not p["done"]:  # the loop moved on: a candidate with no logged step leaves no trace
            if p["steps"]:
                p["done"], p["outcome"] = True, "no decision logged"
            else:
                _circuits.pop(prev, None)
        _circuits[tid] = {"portfolio": portfolio, "symbol": symbol, "origin": origin, "opened": time.time(),
                          "last": time.time(), "steps": [], "done": False, "outcome": None}
        _stats["circuits_opened"] += 1
    return tid


def current() -> str | None:
    return getattr(_local, "trade_id", None)


def end(outcome: str = "") -> None:
    tid = current()
    _local.trade_id = None
    if tid:
        with _LOCK:
            c = _circuits.get(tid)
            if c and not c["done"]:
                c["done"], c["outcome"] = True, outcome or c["outcome"] or "no decision logged"


# ------------------------------------------------------------------ signals
def post(sender: str, market: str, kind: str, confidence: float, ttl_s: float, portfolio: str = "systeme",
         to: str = "", reason: str = "", **extra) -> dict:
    from agent_events import log_event
    return log_event(sender, portfolio, "SIGNAL", market, reason, to=to, kind=kind, confidence=confidence,
                     ttl_s=ttl_s, **extra)


def fresh(market: str | None = None, kind: str | None = None, to: str | None = None) -> list[dict]:
    """Signals still valid (expires_at in the future), newest first."""
    now = time.time()
    with _LOCK:
        rows = [r for r in _signals.values() if _ts(r["expires_at"]) > now
                and (market is None or r.get("symbol") == market) and (kind is None or r.get("kind") == kind)
                and (to is None or r.get("to") in ("", None, to))]
    return sorted(rows, key=lambda r: r["ts"], reverse=True)


def observe(rec: dict) -> None:
    """Called by agent_events.log_event for every message (in the same thread)."""
    if not rec.get("trade_id") and current():
        rec["trade_id"] = current()   # the line is already written: this only feeds the in-memory circuit
    with _LOCK:
        if rec.get("expires_at"):
            _signals[rec["msg_id"]] = rec
            if rec.get("action") == "SIGNAL":
                _stats["signals_posted"] += 1
        tid = rec.get("trade_id")
        c = _circuits.get(tid) if tid else None
        if c and not c["done"]:
            c["last"] = time.time()
            c["steps"].append(f"{rec.get('agent')}:{rec.get('action')}")
            if rec.get("action") in TERMINAL or (rec.get("agent") == "Executor" and rec.get("action") in ("BUY", "SELL")):
                c["done"], c["outcome"] = True, f"{rec.get('agent')} {rec.get('action')}"


# ------------------------------------------------------------------ coordinator
# agents that work in the background and write a file instead of events: one "cycle" event each time their file
# changes, so the trading floor shows them at their desk (Desktop dashboard: no event = "planned")
PULSE = {"ChartAnalyst": "chart_analysis.json", "TopTraders": "top_traders.json", "RiskManager": "risk_manager.json",
         "MacroAnalyst": "macro.json", "SentimentAnalyst": "sentiment.json", "OptionsAnalyst": "options.json",
         "PolymarketAnalyst": "polymarket.json", "NewsAgent": "news_flags.json", "Professor": "professor.json",
         "FilterAudit": "filter_audit.json", "Trending": "trending.json", "MarketContext": "market_context.json",
         "FundingRadar": "radar.json", "RegimeHMM": "regime_hmm.json", "DeskContext": "desk_context.json",
         "FundamentalAnalyst": "fundamentals.json", "ApiKeeper": "api_keeper.json", "Backtester": "backtest_report.json"}
_mtimes: dict[str, float] = {}


def pulse() -> int:
    from agent_events import log_event
    n = 0
    for agent, name in PULSE.items():
        try:
            m = (DATA_DIR / name).stat().st_mtime
        except OSError:
            continue
        if _mtimes.get(agent) and m > _mtimes[agent]:
            log_event(agent, "systeme", "INFO", "", f"cycle termine ({name} mis a jour)", result="cycle")
            n += 1
        _mtimes[agent] = m
    return n


def check() -> dict:
    now = time.time()
    stuck = []
    with _LOCK:
        for mid, r in list(_signals.items()):
            if _ts(r["expires_at"]) <= now:
                del _signals[mid]
                if r.get("action") == "SIGNAL" and not r.get("used"):
                    _stats["signals_expired_unused"] += 1
        for tid, c in list(_circuits.items()):
            limit = STUCK_S["micro"] if c["portfolio"].startswith("scalp") else STUCK_S["default"]
            # stuck = approved by a control agent but never executed nor refused ("nothing waits in the void")
            waiting = any(st.endswith((":APPROVE", ":OK", ":SIGNAL")) for st in c["steps"][-1:])
            if not c["done"] and waiting and now - c["last"] > limit:
                c["done"], c["outcome"] = True, "stuck"
                stuck.append((tid, c))
                _stats["circuits_stuck"] += 1
            if (c["done"] or not c["steps"]) and now - c["last"] > 3600:
                del _circuits[tid]
        open_n = sum(1 for c in _circuits.values() if not c["done"])
        recent = [dict(trade_id=t, **{k: v for k, v in c.items() if k != "steps"}, steps=c["steps"][-8:])
                  for t, c in sorted(_circuits.items(), key=lambda kv: -kv[1]["last"])[:30]]
        snap = {"at": _now_iso(), "signals_alive": len(_signals), "circuits_open": open_n, **_stats,
                "recent_circuits": recent}
    for tid, c in stuck:
        try:
            from agent_events import log_event
            log_event("Coordinator", c["portfolio"], "INFO", c["symbol"],
                      f"circuit bloque : aucune etape depuis {int(now - c['last'])} s apres {c['steps'][-3:]}",
                      result="stuck", trade_id=tid)
        except Exception:
            pass
    try:
        tmp = OUT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snap, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(OUT)
    except Exception:
        pass
    return snap


_started = False


def start(every_s: int = 60) -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        while True:
            try:
                check()
                pulse()
            except Exception:
                pass
            time.sleep(every_s)
    threading.Thread(target=loop, daemon=True, name="Coordinator").start()
