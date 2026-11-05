"""Self-improvement loop, v1 (user request 2026-11-05, paper only): the Professor measures, the
Auditor cuts what loses. Every change -> agent_events (agent Professor/Auditor, action ADJUST).

Every 6 h, from the closed trades of the three ledgers (crypto copy, US stocks, short term):
- stats by portfolio, by copied wallet, by exit rule (TradeManager R1-R10 / stops / source exit),
  by trade type (normal vs SCALP) and by market regime at entry (RegimeAgent events):
  count, win rate, mean net PnL %, total net PnL;
- wallet weights for the crypto copy sizing, in [0.5, 1.5]: EWMA of net PnL % per trade, moved
  only after >= MIN_TRADES_WEIGHT trades and by at most +-20 % per day;
- Auditor: a wallet with >= 50 trades and a negative net expectancy is disabled (no more copies);
  a scalp lane with >= 200 trades and a negative net expectancy goes down to 5 % of capital.
Output: DATA_DIR/professor.json = {at, stats, wallet_weights, disabled_wallets, scalp_share}.

Not in v1 (said plainly): champion / challenger testing of parameter changes, and the measured
effect of each TradeManager rule versus "doing nothing" (needs the price path after each action).
"""
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from agent_events import PATH as EVENTS_PATH, log_event

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "professor.json"
MIN_TRADES_WEIGHT, MIN_TRADES_DISABLE, MIN_TRADES_SCALP = 20, 50, 200
MAX_DAILY_STEP = 0.20
EWMA_ALPHA = 0.1
SCALP_DEFAULT, SCALP_FLOOR = 0.20, 0.05


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _ts(t: dict) -> float:
    for k in ("closed", "closed_at", "time_out", "exit_time", "opened", "time_in", "entry_time"):
        v = t.get(k)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return datetime.strptime(v, "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                pass
    return 0.0


def _open_ts(t: dict) -> float:
    for k in ("opened", "open_time", "time_in", "entry_time"):
        v = t.get(k)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return datetime.strptime(v, "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                pass
    return _ts(t)


def closed_trades() -> list[dict]:
    """Every closed trade, normalised: portfolio, symbol, side, pnl, pnl_pct, wallet, rule, type, opened, closed."""
    out = []
    hl = _json(DATA_DIR / "hl_copy_ledger.json")
    for t in hl.get("trades", []):
        if t.get("pnl") is None:
            continue
        out.append({"portfolio": "crypto_copy", "symbol": t.get("symbol"), "side": t.get("side"),
                    "pnl": float(t["pnl"]), "pnl_pct": float(t.get("pnl_pct") or 0),
                    "wallet": t.get("hl_source_addr", ""), "exit": t.get("exit_reason", ""),
                    "type": t.get("trade_type", "HL_COPY"), "opened": _open_ts(t), "closed": _ts(t)})
    al = _json(DATA_DIR / "alpaca_stocks_ledger.json")
    for t in al.get("closed_trades", []):
        if t.get("pnl") is None:
            continue
        cost = float(t.get("cost") or 0)
        out.append({"portfolio": "actions_us", "symbol": t.get("symbol"), "side": "LONG", "pnl": float(t["pnl"]),
                    "pnl_pct": float(t["pnl"]) / cost * 100 if cost else 0.0, "wallet": "",
                    "exit": t.get("exit_reason", "signal"), "type": t.get("trade_type", "NORMAL"),
                    "opened": _open_ts(t), "closed": _ts(t)})
    sh = _json(DATA_DIR / "meme_sim.json")
    for t in sh.get("trades", []):
        out.append({"portfolio": "court_terme", "symbol": t.get("symbol"), "side": t.get("side", "LONG"),
                    "pnl": float(t.get("pnl") or 0), "pnl_pct": float(t.get("pnl_pct") or 0), "wallet": "",
                    "exit": t.get("reason", ""), "type": t.get("trade_type", "LANE_" + str(t.get("lane", "A"))),
                    "opened": float(t.get("opened") or 0), "closed": float(t.get("closed") or 0)})
    return out


def _rule(exit_reason: str) -> str:
    r = (exit_reason or "").strip()
    for tag in ("R10", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "STOP"):
        if r.startswith(tag + " ") or r.startswith(tag + ":"):
            return tag
    if "source closed" in r or "source" in r.lower():
        return "source"
    if "SL" in r:
        return "hard_sl"
    return "autre"


def _regimes() -> list[tuple[float, str]]:
    """(timestamp, risk_on|risk_off) from the RegimeAgent events, oldest first."""
    out = []
    try:
        for line in EVENTS_PATH.read_text(encoding="utf-8").splitlines():
            if '"RegimeAgent"' not in line:
                continue
            e = json.loads(line)
            out.append((datetime.fromisoformat(e["ts"]).timestamp(), str(e.get("result", ""))))
    except (OSError, ValueError, KeyError):
        pass
    return out


def _regime_at(ts: float, reg: list[tuple[float, str]]) -> str:
    cur = "inconnu"
    for t, r in reg:
        if t > ts:
            break
        cur = r
    return cur


def _stats(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    wins = sum(1 for r in rows if r["pnl"] > 0)
    return {"n": len(rows), "win_rate": round(wins / len(rows) * 100, 1),
            "mean_pnl_pct": round(sum(r["pnl_pct"] for r in rows) / len(rows), 3),
            "total_pnl": round(sum(r["pnl"] for r in rows), 2)}


def run() -> dict:
    prev = _json(OUT)
    trades = sorted(closed_trades(), key=lambda r: r["closed"])
    reg = _regimes()
    groups: dict[str, dict[str, list]] = {"portfolio": {}, "wallet": {}, "rule": {}, "type": {}, "regime": {}}
    for t in trades:
        t["rule"] = _rule(t["exit"])
        t["regime"] = _regime_at(t["opened"], reg)
        for g, key in (("portfolio", t["portfolio"]), ("wallet", t["wallet"]), ("rule", t["rule"]),
                       ("type", f"{t['portfolio']}:{t['type']}"), ("regime", f"{t['portfolio']}:{t['regime']}")):
            if key:
                groups[g].setdefault(key, []).append(t)
    stats = {g: {k: _stats(v) for k, v in d.items()} for g, d in groups.items()}

    # wallet weights (EWMA of net PnL % per trade), bounded and slow
    weights = dict(prev.get("wallet_weights", {}))
    day_ago = time.time() - 86400
    step_base = prev.get("weights_day_start", {}) if prev.get("weights_day_at", 0) > day_ago else dict(weights)
    for w, rows in groups["wallet"].items():
        if len(rows) < MIN_TRADES_WEIGHT:
            continue
        ewma = 0.0
        for r in rows:
            ewma = EWMA_ALPHA * r["pnl_pct"] + (1 - EWMA_ALPHA) * ewma
        target = max(0.5, min(1.5, 1.0 + ewma / 5))     # +5 % mean net per trade -> weight 2 (capped 1.5)
        base = float(step_base.get(w, weights.get(w, 1.0)))
        new = max(base * (1 - MAX_DAILY_STEP), min(base * (1 + MAX_DAILY_STEP), target))
        new = round(max(0.5, min(1.5, new)), 3)
        if abs(new - float(weights.get(w, 1.0))) >= 0.01:
            log_event("Professor", "crypto_copy", "ADJUST", w[:10], f"poids du wallet {weights.get(w, 1.0)} -> {new} "
                      f"(EWMA net {ewma:+.2f} %/trade sur {len(rows)} trades)", score=new, result="poids ajuste")
        weights[w] = new

    # Auditor
    disabled = set(prev.get("disabled_wallets", []))
    for w, rows in groups["wallet"].items():
        s = stats["wallet"][w]
        if s["n"] >= MIN_TRADES_DISABLE and s["mean_pnl_pct"] < 0 and w not in disabled:
            disabled.add(w)
            log_event("Auditor", "crypto_copy", "ADJUST", w[:10], f"wallet coupe : esperance nette {s['mean_pnl_pct']:+.2f} %/trade "
                      f"sur {s['n']} trades", result="desactive")
    scalp_share = dict(prev.get("scalp_share", {}))
    for key, s in stats["type"].items():
        if key.endswith(":SCALP"):
            pf = key.split(":")[0]
            share = scalp_share.get(pf, SCALP_DEFAULT)
            if s["n"] >= MIN_TRADES_SCALP and s["mean_pnl_pct"] < 0 and share > SCALP_FLOOR:
                scalp_share[pf] = SCALP_FLOOR
                log_event("Auditor", pf, "ADJUST", "SCALP", f"scalping reduit a {SCALP_FLOOR:.0%} : esperance nette "
                          f"{s['mean_pnl_pct']:+.3f} %/trade sur {s['n']} trades", result="reduit")

    data = {"at": time.time(), "trades": len(trades), "stats": stats, "wallet_weights": weights,
            "disabled_wallets": sorted(disabled), "scalp_share": scalp_share,
            "weights_day_at": prev.get("weights_day_at", time.time()) if prev.get("weights_day_at", 0) > day_ago else time.time(),
            "weights_day_start": step_base}
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(OUT)
    log_event("Professor", "systeme", "INFO", "", f"bilan : {len(trades)} trades clos analyses, "
              f"{len(weights)} wallets ponderes, {len(disabled)} coupes")
    return data


def wallet_weight(addr: str) -> float:
    return float(_json(OUT).get("wallet_weights", {}).get(addr, 1.0))


def wallet_disabled(addr: str) -> bool:
    return addr in set(_json(OUT).get("disabled_wallets", []))


def scalp_share(portfolio: str) -> float:
    return float(_json(OUT).get("scalp_share", {}).get(portfolio, SCALP_DEFAULT))


_started = False


def start(every_s: int = 6 * 3600) -> None:
    global _started
    if _started or not int(os.environ.get("PROFESSOR_ENABLED", 1)):
        return
    _started = True

    def loop():
        time.sleep(300)
        while True:
            try:
                run()
            except Exception as e:
                log_event("Professor", "systeme", "INFO", "", f"erreur interne {type(e).__name__}: {e}")
            time.sleep(every_s)

    threading.Thread(target=loop, daemon=True, name="professor").start()
