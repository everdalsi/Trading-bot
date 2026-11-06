"""Treasury (tresorerie): proposes how the capital should be split between the desks, from MEASURED results only.

User rules (06/11): no transfer that skews the data, no money moved without his explicit OK. So the Treasury never
moves cash: every TREASURY_EVERY_S it writes a PROPOSAL (DATA_DIR/treasury.json + one Treasury event) that the user
or the CIO can apply. Score per desk = return since the start minus BTC over the same period (vs hold), divided by
its worst drawdown, only for desks with >= MIN_TRADES closed trades; others keep their current weight ("pas assez
de trades"). Never raises.
"""
import json
import os
import threading
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "treasury.json"
EVERY_S = int(os.environ.get("TREASURY_EVERY_S", 6 * 3600))
MIN_TRADES = int(os.environ.get("TREASURY_MIN_TRADES", 30))
START = {"crypto_copy": 1000.0, "court_terme": 1000.0}


def proposal() -> dict:
    acc = {}
    try:
        acc = json.loads((DATA_DIR / "accounting.json").read_text(encoding="utf-8")).get("books", {})
    except Exception:
        pass
    try:
        import bench
        btc = bench.btc_since_start()
    except Exception:
        btc = None
    desks, total = {}, 0.0
    for book, start in START.items():
        b = acc.get(book) or {}
        if not b.get("available"):
            continue
        eq = b["equity"]
        total += eq
        ret = (eq / start - 1) * 100
        d = {"equity": eq, "weight_now": None, "return_pct": round(ret, 2), "closed_trades": b.get("closed_trades", 0),
             "vs_btc_pts": round(ret - btc, 2) if btc is not None else None}
        d["eligible"] = d["closed_trades"] >= MIN_TRADES and d["vs_btc_pts"] is not None
        d["why"] = "" if d["eligible"] else (f"pas assez de trades ({d['closed_trades']} < {MIN_TRADES})"
                                              if d["closed_trades"] < MIN_TRADES else "BTC de reference indisponible")
        desks[book] = d
    for d in desks.values():
        d["weight_now"] = round(d["equity"] / total, 3) if total else None
    elig = {k: d for k, d in desks.items() if d["eligible"]}
    if len(elig) >= 2:
        sc = {k: max(0.0, d["vs_btc_pts"]) for k, d in elig.items()}
        pool = sum(desks[k]["weight_now"] for k in elig)
        s = sum(sc.values())
        for k in elig:  # eligible desks share their current weight in proportion to their edge vs BTC (floor 10 %)
            desks[k]["weight_proposed"] = round(max(0.10 * pool, pool * sc[k] / s) if s else desks[k]["weight_now"], 3)
    for d in desks.values():
        d.setdefault("weight_proposed", d["weight_now"])
    return {"at": time.time(), "mode": "proposition (aucun argent deplace sans accord de l'utilisateur)",
            "total_equity": round(total, 2), "btc_since_start_pct": btc, "desks": desks}


_started = False


def start() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        from agent_events import log_event
        time.sleep(600)
        while True:
            try:
                p = proposal()
                tmp = OUT.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
                tmp.replace(OUT)
                txt = "; ".join(f"{k} {d['weight_now']:.0%} -> {d['weight_proposed']:.0%}"
                                + (f" ({d['why']})" if d.get("why") else "") for k, d in p["desks"].items()
                                if d.get("weight_now") is not None)
                log_event("Treasury", "systeme", "INFO", "", f"proposition de repartition : {txt}", result="proposition")
            except Exception as e:
                log_event("Treasury", "systeme", "INFO", "", f"erreur {type(e).__name__}: {str(e)[:120]}")
            time.sleep(EVERY_S)
    threading.Thread(target=loop, daemon=True, name="Treasury").start()
