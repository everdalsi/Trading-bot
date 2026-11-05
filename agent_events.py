"""Standard decision journal: one JSON line per decision of any agent, in DATA_DIR/agent_events.jsonl.

Read live by the trading dashboard (Desktop instance, read-only SSH key -> `events [N]`).
Fields: ts (UTC ISO), agent, portfolio, action, symbol, reason, score, result (+ any extra field).
- agent: who decided (RegimeAgent, SignalAgent, DrawdownGuard, CorrelationWatcher, Executor...)
- portfolio: "actions_us" | "crypto_copy" | "court_terme" | "micro"
- action: BUY | SELL | SKIP | VETO | REDUCE | HALT | SIGNAL | OBSERVE | INFO
Never raises: a journal failure must never touch trading. The file rotates at ~20 MB.
"""
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

PATH = Path(os.environ.get("DATA_DIR") or ".") / "agent_events.jsonl"
MAX_BYTES = 20 * 1024 * 1024
_LOCK = threading.Lock()


def log_event(agent: str, portfolio: str, action: str, symbol: str = "", reason: str = "",
              score=None, result=None, **extra) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "agent": agent,
           "portfolio": portfolio, "action": action, "symbol": symbol, "reason": str(reason)[:300],
           "score": score, "result": result, **extra}
    try:
        line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
        with _LOCK:
            if PATH.exists() and PATH.stat().st_size > MAX_BYTES:
                PATH.replace(PATH.with_suffix(".jsonl.1"))
            with open(PATH, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass
