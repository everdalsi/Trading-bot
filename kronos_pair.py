"""KronosPair (06/11, user: "l'agent de kronos toujours accompagne d'un agent qui suit les 100 meilleurs wallets en
fonction des categories de trade qu'on veut prendre").

After every Kronos round, for each of the 100 markets (universe A = crypto copy, B = short term):
- Kronos: 4 h direction, expected move, share of sampled paths that agree;
- smart money of THAT universe's category: Hyperliquid best-wallet consensus (top_traders.json "consensus_A" /
  "consensus_B" once TopTraders tracks 100 wallets per category, else the global "consensus") in [-1, +1];
- Binance top traders' positioning (public_data.json top_long_pct: > 55 % long / < 45 % short);
-> accord = "aligne" (Kronos and smart money agree), "oppose", or "inconnu" (missing data stays missing).
Measurement first: every pairing is stored with Kronos' own forecast id (sym, t0) and scored from kronos_track.json
once the real 4 h move is known: hit rate when aligned vs when opposed. Signals go out only when Kronos itself is
trusted AND the pairing is aligned; before that, confidence 0.
Output DATA_DIR/kronos_pair.json.
"""
import json
import os
import threading
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "kronos_pair.json"
TRACK = DATA_DIR / "kronos_pair_track.json"
_started = False


def _read(name: str) -> dict:
    try:
        return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def smart_direction(wallets: float | None, top_long_pct: float | None) -> int | None:
    """+1 / -1 / 0 from the wallet consensus and the Binance top traders; None when both are missing."""
    votes = []
    if wallets is not None:
        votes.append(1 if wallets > 0.2 else -1 if wallets < -0.2 else 0)
    if top_long_pct is not None:
        votes.append(1 if top_long_pct > 55 else -1 if top_long_pct < 45 else 0)
    if not votes:
        return None
    s = sum(votes)
    return 1 if s > 0 else -1 if s < 0 else 0


def pair(kronos: dict, top: dict, public: dict) -> dict:
    out = {}
    for sym, k in (kronos.get("markets") or {}).items():
        uni = k.get("universe") or "A"
        coin = sym[:-4]
        cons = (top.get(f"consensus_{uni}") or top.get("consensus") or {}).get(coin)
        pub = public.get(sym) or {}
        r4 = (k.get("pred_ret_pct") or {}).get("4")
        kdir = None if r4 is None else (1 if r4 > 0 else -1 if r4 < 0 else 0)
        sdir = smart_direction(cons, pub.get("top_long_pct"))
        accord = "inconnu" if kdir is None or sdir in (None, 0) or kdir == 0 else ("aligne" if kdir == sdir else "oppose")
        out[sym] = {"universe": uni, "t0": k.get("t0"), "kronos_dir": kdir, "kronos_r4": r4, "agree": k.get("agree"),
                    "wallets": cons, "top_long_pct": pub.get("top_long_pct"), "smart_dir": sdir, "accord": accord}
    return out


def score(track: list[dict], ktrack: list[dict]) -> dict:
    """Hit rate of Kronos' 4 h direction when aligned vs opposed to the smart money (real moves from kronos_track)."""
    real = {(r["sym"], r["t0"]): (r.get("real") or {}).get("4") for r in ktrack}
    st = {}
    for acc in ("aligne", "oppose"):
        rows = [(p["kronos_dir"], real.get((p["sym"], p["t0"]))) for p in track if p["accord"] == acc]
        rows = [(d, y) for d, y in rows if y is not None and d]
        st[acc] = {"n": len(rows), "hit": round(sum((y > 0) == (d > 0) for d, y in rows) / len(rows), 3) if rows else None}
    return st


def run_once() -> dict:
    kr = _read("kronos.json")
    if not kr.get("markets"):
        return {}
    import public_data
    markets = pair(kr, _read("top_traders.json"), public_data.load())
    try:
        track = json.loads(TRACK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        track = []
    seen = {(p["sym"], p["t0"]) for p in track}
    keep_after = (time.time() - 30 * 86400) * 1000
    track = [p for p in track if (p.get("t0") or 0) > keep_after]
    for sym, m in markets.items():
        if m["t0"] and (sym, m["t0"]) not in seen and m["accord"] != "inconnu":
            track.append({"sym": sym, "t0": m["t0"], "kronos_dir": m["kronos_dir"], "accord": m["accord"]})
    try:
        ktrack = json.loads((DATA_DIR / "kronos_track.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ktrack = []
    stats = score(track, ktrack if isinstance(ktrack, list) else [])
    data = {"at": time.time(), "kronos_trusted": bool(kr.get("trusted")), "markets": markets, "stats": stats,
            "counts": {a: sum(m["accord"] == a for m in markets.values()) for a in ("aligne", "oppose", "inconnu")}}
    for path, obj in ((TRACK, track), (OUT, data)):
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(obj), encoding="utf-8")
        tmp.replace(path)
    _post(markets, data["kronos_trusted"])
    return data


def _post(markets: dict, trusted: bool, top: int = 5) -> None:
    """Aligned pairs as SIGNAL messages to the right desk; confidence 0 until Kronos itself is trusted."""
    try:
        import signals
        best = sorted((m for m in markets.values() if m["accord"] == "aligne" and m["kronos_r4"] is not None),
                      key=lambda m: -abs(m["kronos_r4"]) * (m.get("agree") or 0))[:top]
        for m in best:
            sym = next(s for s, x in markets.items() if x is m)
            signals.post("KronosPair", sym, "forecast_smart_money", round(min(1.0, abs(m["kronos_r4"]) / 2), 2) if trusted else 0.0,
                         3600, to="FillEngine" if m["universe"] == "A" else "ShortSleeve", universe=m["universe"],
                         reason=f"Kronos 4 h {m['kronos_r4']:+.2f} % + meilleurs traders du meme cote"
                                + ("" if trusted else " - mesure"))
    except Exception:
        pass


def start(every_s: int = 900) -> None:
    global _started
    if _started or not int(os.environ.get("KRONOS_PAIR_ENABLED", 1)):
        return
    _started = True
    from agent_events import log_event

    def loop():
        time.sleep(240)
        last = 0
        while True:
            try:
                kat = _read("kronos.json").get("at") or 0
                if kat > last:                                   # once per Kronos round
                    d = run_once()
                    last = kat
                    if d:
                        c, s = d["counts"], d["stats"]
                        log_event("KronosPair", "analyse", "INFO", "",
                                  f"Kronos + meilleurs traders : {c['aligne']} alignes, {c['oppose']} opposes, "
                                  f"{c['inconnu']} inconnus ; reussite 4 h alignes {s['aligne']['hit']} (n={s['aligne']['n']}), "
                                  f"opposes {s['oppose']['hit']} (n={s['oppose']['n']})")
            except Exception as e:
                log_event("KronosPair", "analyse", "INFO", "", f"erreur interne {type(e).__name__}: {str(e)[:200]}")
            time.sleep(every_s)

    threading.Thread(target=loop, daemon=True, name="kronos_pair").start()
