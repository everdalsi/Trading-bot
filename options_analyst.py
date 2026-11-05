"""OptionsAnalyst (trading v3) -- REVIVES the existing agents/options_flow_agent.py (OptionsFlowAgent, dead since the
MICRO orchestrator was switched off) instead of writing a duplicate. MEASUREMENT MODE: recorded next to every decision,
no weight until the IC monitor shows it predicts.

Reason to exist: the options market shows what big players expect: fear of a drop (puts dearer than calls = skew),
expected volatility (ATM IV), positioning (put/call open interest). No live agent reads it.
Frame: Deribit public API, every 30 min, BTC and ETH, never trades, never vetoes.
Function: DATA_DIR/options.json = {at, coins: {BTCUSDT: {score -1..+1, pcr, iv_atm, skew_10pct, flow_agent_score,
signals}}} + SIGNAL in agent_events when a coin's score moves by >= 0.4.

Calculation:
  flow_agent_score  the existing OptionsFlowAgent score (BTC only: put/call OI ratio contrarian + average IV), mapped to -1..+1
  skew_10pct        nearest expiry 7-45 days: IV of the put ~10 % below spot minus IV of the call ~10 % above spot (vol points).
                    Puts much dearer = fear (negative for price short term, contrarian when extreme): measured both ways
  iv_atm            IV of the strike nearest to spot on that expiry
  score             = mean(flow_agent_score, -clip(skew/10)) -> recorded only
"""
import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from agent_events import log_event

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "options.json"
API = "https://www.deribit.com/api/v2/public"
MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


def _expiry(code: str) -> datetime | None:
    try:  # e.g. 13NOV26
        return datetime(2000 + int(code[-2:]), MONTHS[code[-5:-2]], int(code[:-5]), 8, tzinfo=timezone.utc)
    except Exception:
        return None


def surface(currency: str) -> dict:
    rows = requests.get(f"{API}/get_book_summary_by_currency", params={"currency": currency, "kind": "option"},
                        timeout=10).json().get("result", [])
    now = datetime.now(timezone.utc)
    by_exp: dict[str, list] = {}
    spot = None
    for r in rows:
        try:
            _, code, strike, kind = r["instrument_name"].split("-")
        except ValueError:
            continue
        exp = _expiry(code)
        if not exp or not r.get("mark_iv"):
            continue
        days = (exp - now).total_seconds() / 86400
        spot = spot or r.get("underlying_price")
        by_exp.setdefault(code, []).append((days, float(strike), kind, float(r["mark_iv"]), float(r.get("open_interest", 0))))
    cand = sorted((v[0][0], k) for k, v in by_exp.items() if 7 <= v[0][0] <= 45)
    if not cand or not spot:
        return {}
    days, code = cand[0]
    opts = by_exp[code]

    def iv_near(target: float, kind: str):
        xs = [o for o in opts if o[2] == kind]
        return min(xs, key=lambda o: abs(o[1] - target))[3] if xs else None

    put10, call10 = iv_near(spot * 0.9, "P"), iv_near(spot * 1.1, "C")
    atm = iv_near(spot, "C")
    put_oi = sum(o[4] for o in opts if o[2] == "P")
    call_oi = sum(o[4] for o in opts if o[2] == "C")
    return {"expiry": code, "days": round(days, 1), "spot": round(spot, 2), "iv_atm": atm,
            "skew_10pct": None if put10 is None or call10 is None else round(put10 - call10, 2),
            "pcr_expiry": round(put_oi / call_oi, 3) if call_oi else None}


def flow_agent_score() -> tuple[float | None, list[str], dict]:
    """The existing OptionsFlowAgent (BTC), run on its own (no orchestrator)."""
    try:
        from agents.options_flow_agent import OptionsFlowAgent
        s, signals, metrics = asyncio.run(OptionsFlowAgent()._analyze_options_flow())
        return round((s - 0.5) * 2, 3), signals, metrics
    except Exception as e:
        return None, [f"OptionsFlowAgent indisponible : {type(e).__name__}"], {}


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run() -> dict:
    prev = _json(OUT).get("coins", {})
    coins = {}
    fa, fa_signals, fa_metrics = flow_agent_score()
    for cur, sym in (("BTC", "BTCUSDT"), ("ETH", "ETHUSDT")):
        try:
            s = surface(cur)
        except Exception as e:
            s = {"error": f"{type(e).__name__}: {str(e)[:80]}"}
        parts = []
        if s.get("skew_10pct") is not None:
            parts.append(_clip(-s["skew_10pct"] / 10))
        if sym == "BTCUSDT" and fa is not None:
            parts.append(fa)
            s.update({"flow_agent_score": fa, "pcr_all": fa_metrics.get("put_call_ratio"), "signals": fa_signals})
        s["score"] = round(sum(parts) / len(parts), 3) if parts else None
        coins[sym] = s
        old = (prev.get(sym) or {}).get("score")
        if s["score"] is not None and old is not None and abs(s["score"] - old) >= 0.4:
            log_event("OptionsAnalyst", "marche", "SIGNAL", sym, f"options {old:+.2f} -> {s['score']:+.2f} (skew "
                      f"{s.get('skew_10pct')} pts, IV ATM {s.get('iv_atm')} %)", score=s["score"])
    data = {"at": time.time(), "coins": coins, "mode": "mesure (aucun poids)"}
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    return data


def read(symbol: str) -> dict:
    """For the SeniorExpert: options view on BTC/ETH (other coins: BTC as market proxy). Recorded, no weight."""
    c = _json(OUT).get("coins", {})
    x = c.get(symbol) or c.get("BTCUSDT") or {}
    return {"options_score": x.get("score"), "options_skew": x.get("skew_10pct"), "options_iv_atm": x.get("iv_atm")}


_started = False


def start(every_s: int = 1800) -> None:
    global _started
    if _started or not int(os.environ.get("OPTIONS_ANALYST_ENABLED", 1)):
        return
    _started = True

    def loop():
        time.sleep(240)
        while True:
            try:
                run()
            except Exception as e:
                log_event("OptionsAnalyst", "systeme", "INFO", "", f"erreur interne {type(e).__name__}: {str(e)[:150]}")
            time.sleep(every_s)

    threading.Thread(target=loop, daemon=True, name="options_analyst").start()


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, ensure_ascii=False)[:1500])
