"""Standard decision journal: one JSON line per decision of any agent, in DATA_DIR/agent_events.jsonl.

Read live by the trading dashboard (Desktop instance, read-only SSH key -> `events [N]`).
Fields: ts (UTC ISO), agent, portfolio, action, symbol, reason, score, result (+ any extra field).
- agent: who decided (RegimeAgent, SignalAgent, DrawdownGuard, CorrelationWatcher, Executor...)
- portfolio: "actions_us" | "crypto_copy" | "court_terme" | "micro"
- action: BUY | SELL | SKIP | VETO | REDUCE | HALT | SIGNAL | OBSERVE | INFO
Never raises: a journal failure must never touch trading. The file rotates at ~20 MB.

Standard message format (desk spec 06/11, step 1), optional fields on every line, so old readers keep working:
- msg_id (auto), to (recipient agent or desk, "" = broadcast), kind (signal type), confidence (0-1),
  expires_at (UTC ISO, from ttl_s: an expired signal must not be used), trade_id (links every step of one trade:
  signal -> data quality -> security -> devil's advocate -> risk -> desk decision -> execution -> accounting -> Professor).
signals.py is the bus built on it (post / fresh signals / circuit check).
"""
import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path(os.environ.get("DATA_DIR") or ".") / "agent_events.jsonl"
MAX_BYTES = 20 * 1024 * 1024
_LOCK = threading.Lock()


def log_event(agent: str, portfolio: str, action: str, symbol: str = "", reason: str = "",
              score=None, result=None, to: str | None = None, kind: str | None = None,
              confidence: float | None = None, ttl_s: float | None = None, trade_id: str | None = None,
              **extra) -> dict:
    now = datetime.now(timezone.utc)
    rec = {"ts": now.isoformat(timespec="seconds"), "agent": agent,
           "portfolio": portfolio, "action": action, "symbol": symbol, "reason": str(reason)[:300],
           "score": score, "result": result, **extra}
    rec["msg_id"] = uuid.uuid4().hex[:12]
    for k, v in (("to", to), ("kind", kind), ("trade_id", trade_id)):
        if v:
            rec[k] = v
    if confidence is not None:
        rec["confidence"] = round(max(0.0, min(1.0, float(confidence))), 3)
    if ttl_s:
        rec["expires_at"] = (now + timedelta(seconds=float(ttl_s))).isoformat(timespec="seconds")
    try:
        line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
        with _LOCK:
            if PATH.exists() and PATH.stat().st_size > MAX_BYTES:
                PATH.replace(PATH.with_suffix(".jsonl.1"))
            with open(PATH, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass
    try:
        import signals  # in-memory bus (fresh signals, circuit check); never blocks the journal
        signals.observe(rec)
    except Exception:
        pass
    return rec
