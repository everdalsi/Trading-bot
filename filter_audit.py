"""Filter audit (Desktop, 06/11): did the new short-term filters help? Run every 6 h next to the Professor.

Scores what orderflow.py and microstructure.py logged, against what the price actually did next (Binance spot
1-min closes), so a threshold is only tightened or relaxed on evidence:

- orderflow: for each evaluated entry, the side-signed return over the next HORIZON_M minutes minus the round
  trip cost. Allowed entries should beat blocked ones; if blocked ones do better, the filter is costing money.
- scalp_mode: did the tape do what the mode said? "breakout" -> the last HORIZON_M-min move continues,
  "mean_reversion" -> it reverses. Hit rate per mode (50 % = no information).

    python filter_audit.py            -> prints and writes DATA_DIR/filter_audit.json (dashboard / Professor)
Never raises on bad rows; entries younger than HORIZON_M minutes are skipped (outcome not known yet).
"""
import json
import os
import time
from pathlib import Path

import requests

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
SPOT = "https://api.binance.com"
HORIZON_M = int(os.environ.get("AUDIT_HORIZON_M", 3))
COST = float(os.environ.get("AUDIT_COST", 0.0024))   # 0.10 %/side taker + ~0.02 % slippage, both sides
LOOKBACK_H = int(os.environ.get("AUDIT_LOOKBACK_H", 48))
_closes: dict[str, dict[int, float]] = {}


def _read(name: str) -> list[dict]:
    rows, since = [], time.time() - LOOKBACK_H * 3600
    try:
        for line in (DATA_DIR / name).read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                if since <= r.get("ts", 0) <= time.time() - (HORIZON_M + 1) * 60:
                    rows.append(r)
            except Exception:
                continue
    except Exception:
        pass
    return rows


def _close_at(symbol: str, ts: float) -> float | None:
    """Close of the 1-min bar containing ts (minute bars fetched in 1000-bar pages and cached)."""
    m = int(ts // 60) * 60_000
    cache = _closes.setdefault(symbol, {})
    if m not in cache:
        try:
            k = requests.get(f"{SPOT}/api/v3/klines", timeout=10,
                             params={"symbol": symbol, "interval": "1m", "startTime": m - 10 * 60_000, "limit": 1000}).json()
            cache.update({int(x[0]): float(x[4]) for x in k})
        except Exception:
            return None
    return cache.get(m)


def _fwd(symbol: str, ts: float) -> tuple[float, float] | None:
    """(return over the previous HORIZON_M minutes, return over the next HORIZON_M minutes)."""
    p0, p_prev, p_next = _close_at(symbol, ts), _close_at(symbol, ts - HORIZON_M * 60), _close_at(symbol, ts + HORIZON_M * 60)
    if not (p0 and p_prev and p_next):
        return None
    return p0 / p_prev - 1, p_next / p0 - 1


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 5) if xs else None


def audit_orderflow() -> dict:
    out = {"allowed": [], "blocked": []}
    for r in _read("orderflow_log.jsonl"):
        if "tfi" not in r:
            continue
        f = _fwd(r["symbol"], r["ts"])
        if f is None:
            continue
        sign = 1 if r["side"].upper() == "LONG" else -1
        out["allowed" if r.get("ok") else "blocked"].append(sign * f[1] - COST)
    res = {k: {"n": len(v), "avg_net": _mean(v), "win_rate": _mean([float(x > 0) for x in v])} for k, v in out.items()}
    a, b = res["allowed"]["avg_net"], res["blocked"]["avg_net"]
    res["verdict"] = ("pas assez de donnees" if min(res["allowed"]["n"], res["blocked"]["n"]) < 30 else
                      "le filtre aide" if a is not None and b is not None and a > b else "le filtre coute : a relacher")
    return res


def audit_scalp_mode() -> dict:
    hits: dict[str, list[float]] = {}
    for r in _read("microstructure_log.jsonl"):
        if r.get("kind") != "scalp_mode" or r.get("mode") not in ("breakout", "mean_reversion", "skip"):
            continue
        f = _fwd(r["symbol"], r["ts"])
        if f is None or f[0] == 0 or f[1] == 0:
            continue
        cont = (f[0] > 0) == (f[1] > 0)
        hit = cont if r["mode"] == "breakout" else (not cont) if r["mode"] == "mean_reversion" else cont
        hits.setdefault(r["mode"], []).append(float(hit))
    return {m: {"n": len(v), "hit_rate": _mean(v)} for m, v in hits.items()}


def run() -> dict:
    res = {"ts": time.time(), "horizon_min": HORIZON_M, "cost": COST,
           "orderflow": audit_orderflow(), "scalp_mode": audit_scalp_mode()}
    try:
        (DATA_DIR / "filter_audit.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    return res


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
