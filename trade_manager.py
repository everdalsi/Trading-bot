"""TradeManager (spec A of ~/.claude/plans/trading-moteur-gestion-et-infos.md): rules R1-R10 on
OPEN positions. It never opens nor adds to a position (that is the SeniorExpert's job).

The engine is generic: each portfolio calls manage() from its own loop (same thread as its other
ledger writes, so no race) with a view of its positions and two callbacks that execute a close or
a partial reduction through the portfolio's normal exit path (same simulated costs as any sale).

Rules, first match wins, one action per position per cycle, at most 2 actions per position per
24 h except forced exits (R2, R3, stops). Thresholds in DATA_DIR/trade_rules.json (created with
the defaults below, editable without redeploying). Every action -> agent_events (agent
TradeManager, rule in "rule") and the position's "management_log" (for the Professor's
with/without measurement on the next price).

R1 bad data (DataGuard blocking > 10 min)  -> nothing, logged
R2 copied wallet left / cut its position by >= half -> close
R3 catastrophe stop (-8 % crypto, -6 % stocks)      -> close
   stops set by earlier rules (entry stop, trailing, support stop) -> close
R4 regime turns down (BTC < MA50 and MA50 falling), long  -> reduce 50 %, stop to entry
R5 strong negative news < 1 h on the symbol          -> reduce 50 %, close if already reduced
R6 top-50 consensus against us <= -0.4 (once)        -> reduce 50 %
R7 gain >= +6 % (or price at the first 1 h resistance) -> take 1/3, trailing stop 3 %
R8 gain >= +12 % and consensus >= +0.4 with us       -> trailing stop widened to 5 % (no sale)
R9 open > 72 h (crypto) / 10 days (stocks), PnL within +-1 %  -> close
R10 portfolio drawdown >= 10 % from its peak          -> reduce 50 % the 3 worst positions (1 per 24 h)
"""
import json
import os
import time
from pathlib import Path

import requests

from agent_events import log_event

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
RULES_PATH = DATA_DIR / "trade_rules.json"
DEFAULT_RULES = {
    "enabled": True,
    "r1_bad_data_min": 10,
    "r2_source_cut_ratio": 0.5,
    "r3_stop_crypto_pct": -8.0, "r3_stop_stocks_pct": -6.0,
    "r4_reduce": 0.5,
    "r5_news_window_min": 60, "r5_reduce": 0.5,
    "r6_consensus": -0.4, "r6_reduce": 0.5,
    "r7_gain_pct": 6.0, "r7_take": 1 / 3, "r7_trail_pct": 3.0,
    "r8_gain_pct": 12.0, "r8_consensus": 0.4, "r8_trail_pct": 5.0,
    "r9_hours_crypto": 72, "r9_days_stocks": 10, "r9_flat_pct": 1.0,
    "r10_dd_pct": 10.0, "r10_reduce": 0.5, "r10_worst": 3,
    "max_actions_24h": 2,
    "support_stop_atr": 0.5,
}
FORCED = {"R2", "R3", "STOP"}


def rules() -> dict:
    try:
        user = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        user = {}
        try:
            RULES_PATH.write_text(json.dumps(DEFAULT_RULES, indent=1), encoding="utf-8")
        except OSError:
            pass
    return {**DEFAULT_RULES, **user}


# ---------------------------------------------------------------- inputs from the other agents

_regime = {"at": 0.0, "down": False, "why": ""}


def regime_down() -> tuple[bool, str]:
    """BTC daily close < MA50 and MA50 lower than 5 days ago (cached 1 h)."""
    if time.time() - _regime["at"] > 3600:
        try:
            k = requests.get("https://api.binance.com/api/v3/klines",
                             params={"symbol": "BTCUSDT", "interval": "1d", "limit": 60}, timeout=15).json()
            c = [float(x[4]) for x in k[:-1]]
            ma = sum(c[-50:]) / 50
            ma_prev = sum(c[-55:-5]) / 50
            down = c[-1] < ma and ma < ma_prev
            _regime.update(at=time.time(), down=down,
                           why=f"BTC {c[-1]:,.0f} {'<' if c[-1] < ma else '>'} MM50 {ma:,.0f}, MM50 {'en baisse' if ma < ma_prev else 'en hausse'}")
            log_event("RegimeAgent", "marche", "SIGNAL", "BTC", _regime["why"], result="risk_off" if down else "risk_on")
        except Exception:
            pass
    return _regime["down"], _regime["why"]


def _json(name: str) -> dict:
    try:
        return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def news_negative(symbol: str, window_s: float) -> str:
    """Title of a strong negative news flag on this symbol within the window, else ''."""
    base = symbol.replace("USDT", "")
    flags = (_json("news_flags.json").get("symbols") or {})
    for f in flags.get(symbol, []) + flags.get(base, []):
        if f.get("level") == "negatif_fort" and time.time() - float(f.get("at", 0)) <= window_s:
            return f.get("title", "drapeau negatif fort")
    return ""


def consensus(symbol: str) -> float | None:
    c = (_json("top_traders.json").get("consensus") or {})
    v = c.get(symbol, c.get(symbol.replace("USDT", "")))
    return float(v) if isinstance(v, (int, float)) else None


# ---------------------------------------------------------------- engine

def _pnl_pct(side: str, entry: float, price: float) -> float:
    return ((entry - price) / entry if side == "SHORT" else (price - entry) / entry) * 100


def _against(side: str, price: float, level: float) -> bool:
    """True when the price crossed a stop level against the position."""
    return price >= level if side == "SHORT" else price <= level


def decide(view: dict, r: dict, now: float | None = None) -> tuple[str, str, float, str, dict]:
    """(rule, action CLOSE|REDUCE|ADJUST|NONE, fraction, reason, state updates) for one position.
    view: symbol, side, entry, price, opened_at, kind ("crypto"|"stocks"), state (tm_* fields),
    data_bad_s, source_ratio (copy only), support/atr/resistance (ChartAnalyst, optional)."""
    now = now or time.time()
    st, side, entry, price = view["state"], view["side"], view["entry"], view["price"]
    pnl = _pnl_pct(side, entry, price)
    best = st.get("tm_best") or entry
    best = max(best, price) if side != "SHORT" else min(best, price)
    upd = {"tm_best": best}
    if view.get("data_bad_s", 0) > r["r1_bad_data_min"] * 60:
        return "R1", "NONE", 0, f"donnees douteuses depuis {int(view['data_bad_s'] / 60)} min", upd
    ratio = view.get("source_ratio")
    if ratio is not None and ratio < r["r2_source_cut_ratio"]:
        return "R2", "CLOSE", 1, f"le wallet copie est sorti ou a coupe sa position (reste {ratio:.0%})", upd
    stop3 = r["r3_stop_crypto_pct"] if view["kind"] == "crypto" else r["r3_stop_stocks_pct"]
    if pnl <= stop3:
        return "R3", "CLOSE", 1, f"stop catastrophe : {pnl:+.2f} % <= {stop3:.0f} %", upd
    stop = st.get("tm_stop")
    if stop and _against(side, price, stop):
        return "STOP", "CLOSE", 1, f"stop {st.get('tm_stop_why', '')} touche a {stop:.6g} ({pnl:+.2f} %)", upd
    trail = st.get("tm_trail")
    if trail:
        level = best * (1 - trail / 100) if side != "SHORT" else best * (1 + trail / 100)
        if _against(side, price, level):
            return "STOP", "CLOSE", 1, f"stop suiveur {trail:.0f} % depuis le meilleur prix {best:.6g} ({pnl:+.2f} %)", upd
    # support stop from ChartAnalyst, only when tighter than the current stop (longs)
    sup, atr = view.get("support"), view.get("atr")
    if side != "SHORT" and sup and atr:
        lvl = sup - r["support_stop_atr"] * atr
        if lvl < price and lvl > (stop or 0) and lvl > entry * (1 + stop3 / 100):
            upd.update(tm_stop=lvl, tm_stop_why="sous support")
    recent = [t for t in st.get("tm_actions", []) if now - t < 86400]
    capped = len(recent) >= r["max_actions_24h"]
    down, why = view.get("regime_down", (False, ""))
    if not capped and down and side != "SHORT" and not st.get("tm_reduced"):
        upd.update(tm_reduced=True, tm_stop=max(stop or 0, entry), tm_stop_why="au prix d'entree")
        return "R4", "REDUCE", r["r4_reduce"], f"regime baissier ({why})", upd
    news = view.get("news", "")
    if not capped and news:
        if st.get("tm_reduced"):
            return "R5", "CLOSE", 1, f"news negative forte (deja reduite) : {news[:120]}", upd
        upd["tm_reduced"] = True
        return "R5", "REDUCE", r["r5_reduce"], f"news negative forte : {news[:120]}", upd
    cons = view.get("consensus")
    if cons is not None and side == "SHORT":
        cons = -cons  # consensus is long-positive; "with us" for a short is negative
    if not capped and cons is not None and cons <= r["r6_consensus"] and not st.get("tm_r6"):
        upd.update(tm_r6=True, tm_reduced=True)
        return "R6", "REDUCE", r["r6_reduce"], f"consensus top 50 contre nous ({cons:+.2f})", upd
    res = view.get("resistance")
    at_res = side != "SHORT" and res and price >= res and pnl > 0
    if not capped and not st.get("tm_partial") and (pnl >= r["r7_gain_pct"] or at_res):
        upd.update(tm_partial=True, tm_trail=min(st.get("tm_trail") or 99, r["r7_trail_pct"]))
        why7 = f"gain {pnl:+.2f} % >= {r['r7_gain_pct']:.0f} %" if pnl >= r["r7_gain_pct"] else f"1re resistance 1 h atteinte ({res:.6g})"
        return "R7", "REDUCE", r["r7_take"], f"{why7} : prise de 1/3, stop suiveur {r['r7_trail_pct']:.0f} %", upd
    if not capped and pnl >= r["r8_gain_pct"] and cons is not None and cons >= r["r8_consensus"] \
            and (st.get("tm_trail") or 0) < r["r8_trail_pct"]:
        upd["tm_trail"] = r["r8_trail_pct"]
        return "R8", "ADJUST", 0, f"gain {pnl:+.2f} % et consensus {cons:+.2f} : on laisse courir, stop suiveur {r['r8_trail_pct']:.0f} %", upd
    age_h = (now - view["opened_at"]) / 3600
    limit_h = r["r9_hours_crypto"] if view["kind"] == "crypto" else r["r9_days_stocks"] * 24
    if not capped and age_h > limit_h and abs(pnl) <= r["r9_flat_pct"]:
        return "R9", "CLOSE", 1, f"ouverte depuis {age_h:.0f} h sans resultat ({pnl:+.2f} %) : capital immobilise", upd
    return "", "NONE", 0, "", upd


def manage(portfolio: str, views: list[dict], execute, send_fn=None) -> int:
    """Evaluate every position; execute(view, rule, action, fraction, reason) performs it.
    Updates view["state"] in place (caller persists it). Returns the number of actions."""
    r = rules()
    if not r.get("enabled", True):
        return 0
    down = regime_down()
    n = 0
    for v in views:
        v.setdefault("regime_down", down)
        v.setdefault("news", news_negative(v["symbol"], r["r5_news_window_min"] * 60))
        v.setdefault("consensus", consensus(v["symbol"]))
        try:
            rule, action, frac, reason, upd = decide(v, r)
        except Exception as e:
            log_event("TradeManager", portfolio, "INFO", v.get("symbol", ""), f"erreur interne {type(e).__name__}: {e}")
            continue
        v["state"].update(upd)
        if action == "NONE":
            if rule == "R1":
                log_event("TradeManager", portfolio, "SKIP", v["symbol"], reason, rule="R1")
            continue
        now = time.time()
        entry = {"ts": now, "rule": rule, "action": action, "fraction": round(frac, 3), "price": v["price"],
                 "pnl_pct": round(_pnl_pct(v["side"], v["entry"], v["price"]), 3), "reason": reason}
        v["state"].setdefault("management_log", []).append(entry)
        if rule not in FORCED:
            v["state"]["tm_actions"] = [t for t in v["state"].get("tm_actions", []) if now - t < 86400] + [now]
        log_event("TradeManager", portfolio, action, v["symbol"], reason, score=entry["pnl_pct"],
                  result=f"{rule} {action.lower()}" + (f" {frac:.0%}" if action == "REDUCE" else ""),
                  rule=rule, price=v["price"], side=v["side"])
        if action in ("CLOSE", "REDUCE"):
            try:
                execute(v, rule, action, frac, f"{rule} : {reason}")
                n += 1
            except Exception as e:
                log_event("TradeManager", portfolio, "INFO", v["symbol"], f"execution {rule} echouee : {type(e).__name__}: {e}")
    return n


def r10_worst(portfolio: str, views: list[dict], equity: float, peak: float, last_at: float) -> list[dict]:
    """Positions to cut by 50 % when the portfolio is >= 10 % under its peak (once per 24 h)."""
    r = rules()
    if not r.get("enabled", True) or not peak or time.time() - last_at < 86400:
        return []
    dd = (peak - equity) / peak * 100
    if dd < r["r10_dd_pct"]:
        return []
    worst = sorted(views, key=lambda v: _pnl_pct(v["side"], v["entry"], v["price"]))[:int(r["r10_worst"])]
    log_event("TradeManager", portfolio, "REDUCE", ",".join(v["symbol"] for v in worst),
              f"drawdown du portefeuille {dd:.1f} % >= {r['r10_dd_pct']:.0f} %", result="R10 reduce 50% x3", rule="R10")
    return worst
