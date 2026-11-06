"""Trade memory + invalidation check (Desktop, improvement 4 of 06/11). Measurement mode: no weight.

Idea taken from the open-source LLM_trader project (qrak): before an entry, look up the most similar past
setups and how they ended; and every signal must name the price that would prove it wrong.

    record(decision_id, features, invalidation_price) -> None   at entry (features: dict, see FEATURES)
    close(decision_id, net_pct)                         -> None   at exit, net of fees, in %
    similar(features, k=5) -> {"n", "win_rate", "avg_net", "source"} | None
    invalidation_ok(side, entry, invalidation_price, max_dist=0.05) -> (bool, why)

similar() uses the memory's own closed entries (all features) once it holds >= k of them; before that it falls
back to the Professor's closed trades (categorical match on portfolio/strategy/side/regime only), so it is
useful from day one. Never raises: errors return None / True. State: DATA_DIR/trade_memory.jsonl.
"""
import json
import math
import os
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
PATH = DATA_DIR / "trade_memory.jsonl"
CATEGORICAL = ("portfolio", "strategy", "side", "regime")
# numeric features and their typical scale (distance = sum of squared scaled gaps)
NUMERIC = {"rsi": 15.0, "trend_1h": 1.0, "trend_4h": 1.0, "funding_z": 1.0, "vol_pct": 1.0, "tfi": 0.3,
           "score": 2.0}
CAT_PENALTY = 4.0
_ALIASES = {"BUY": "LONG", "SELL": "SHORT"}


def _norm(v) -> str:
    s = str(v or "").upper()
    return _ALIASES.get(s, s)


def _rows() -> list[dict]:
    try:
        rows, outcome = {}, {}
        for line in PATH.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("kind") == "close":
                outcome[r["id"]] = r["net_pct"]
            else:
                rows[r["id"]] = r
        return [dict(r, net_pct=outcome[i]) for i, r in rows.items() if i in outcome]
    except Exception:
        return []


def _append(row: dict) -> None:
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def record(decision_id: str, features: dict, invalidation_price: float | None) -> None:
    _append({"kind": "entry", "id": str(decision_id), "ts": time.time(), "features": features or {},
             "invalidation_price": invalidation_price})


def close(decision_id: str, net_pct: float) -> None:
    try:
        _append({"kind": "close", "id": str(decision_id), "ts": time.time(), "net_pct": float(net_pct)})
    except Exception:
        pass


def _distance(a: dict, b: dict) -> float:
    d = sum(CAT_PENALTY for k in CATEGORICAL if k in a and k in b and _norm(a[k]) != _norm(b[k]))
    for k, scale in NUMERIC.items():
        if isinstance(a.get(k), (int, float)) and isinstance(b.get(k), (int, float)):
            d += ((a[k] - b[k]) / scale) ** 2
    return d


def _summary(nets: list[float], source: str) -> dict:
    return {"n": len(nets), "win_rate": round(sum(x > 0 for x in nets) / len(nets), 3),
            "avg_net": round(sum(nets) / len(nets), 4), "source": source}


def similar(features: dict, k: int = 5) -> dict | None:
    try:
        own = _rows()
        if len(own) >= k:
            best = sorted(own, key=lambda r: _distance(features, r["features"]))[:k]
            return _summary([r["net_pct"] for r in best], "memoire")
        import professor  # fallback: the Professor's closed trades, categorical match only
        reg = professor._regimes()
        pool = []
        for t in professor.closed_trades():
            t = dict(t, strategy=t.get("type"), regime=professor._regime_at(t["opened"], reg))
            match = sum(_norm(t.get(c)) == _norm(features.get(c)) for c in CATEGORICAL
                        if c in features)
            if t.get("pnl_pct") is not None:
                pool.append((match, t["closed"], float(t["pnl_pct"])))
        if not pool:
            return None
        top = max(m for m, _, _ in pool)
        best = sorted([p for p in pool if p[0] == top], key=lambda p: -p[1])[:max(k, 20)]  # most recent first
        return _summary([p[2] for p in best], f"professeur ({top}/{len([c for c in CATEGORICAL if c in features])} criteres)")
    except Exception:
        return None


def invalidation_ok(side: str, entry: float, invalidation_price: float | None, max_dist: float = 0.05) -> tuple[bool, str]:
    """A signal must name the price that proves it wrong: below entry for LONG, above for SHORT, within max_dist."""
    try:
        if invalidation_price is None or not entry or math.isnan(float(invalidation_price)):
            return False, "pas de prix d'invalidation"
        dist = (entry - invalidation_price) / entry if side.upper() == "LONG" else (invalidation_price - entry) / entry
        if dist <= 0:
            return False, f"invalidation du mauvais cote ({invalidation_price} vs entree {entry})"
        if dist > max_dist:
            return False, f"invalidation trop loin ({dist:.1%} > {max_dist:.0%})"
        return True, f"invalidation a {dist:.2%}"
    except Exception:
        return True, "erreur, pas de blocage"
