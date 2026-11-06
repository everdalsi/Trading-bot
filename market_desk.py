"""MarketDesk (06/11, user request "analyser, scanner et prendre des decisions sur 50 marches simultanement").

The 50 most liquid Binance USDT markets (stablecoins, wrapped and leveraged tokens removed; refreshed every 6 h) are
analysed together every 5 minutes. No new data source: the desk reads what the specialists already write
- ChartAnalyst  chart_analysis.json  trend / MACD / RSI / patterns on 5m, 1h, 4h (now run on the 50 markets, in parallel)
- Kronos        kronos.json          candlestick forecast +4 h and the share of sampled paths that agree
- FundingRadar  radar.json           funding (crowded side) and open-interest flow
and turns them into ONE decision per market: LONG / SHORT / WAIT, a confidence 0..1 and the reasons.

Measurement first: every LONG / SHORT decision opens a virtual trade in a shadow book (no capital, stop 1.5 x ATR,
target 3 x ATR, max 12 h, costs deducted). candidates() hands setups to the FillEngine only once the shadow book has
>= 100 closed trades with a positive average after costs. Kronos counts in the score only once kronos_agent says
it is trusted; before that it is shown, not used.

    universe(n=50) -> list[str]
    tick() -> dict                       one analysis round, writes DATA_DIR/market_desk.json (+ market_desk_book.json)
    candidates(min_conf=0.6, side=None, proven_only=True) -> list[dict]
    start(every_s=300)
"""
import json
import os
import threading
import time
from pathlib import Path

import requests

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "market_desk.json"
BOOK = DATA_DIR / "market_desk_book.json"
N_MARKETS = int(os.environ.get("MARKET_DESK_N", 50))
COST_PCT = float(os.environ.get("MARKET_DESK_COST_PCT", 0.10))   # round trip fees + slippage, %
ENTER = float(os.environ.get("MARKET_DESK_ENTER", 0.35))          # |score| needed to decide LONG / SHORT
MAX_HOLD_S = 12 * 3600
PROVEN_N = 100
STABLES = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "EUR", "USD1", "USDE", "PYUSD", "AEUR", "XUSD",
           "RLUSD", "BFUSD", "EURI", "UST", "USTC", "SUSD", "GUSD", "FRAX", "LUSD", "RWUSD", "U"}
WRAPPED = {"WBTC", "WBETH", "BETH", "STETH", "WETH"}
CRYPTO_B = {"ARB", "BNB", "CKB", "DGB", "SHIB", "TRB", "BB"}   # real cryptos whose ticker ends in B
_LOCK = threading.Lock()
_universe: dict = {"at": 0.0, "symbols": []}
_started = False


# ---------------------------------------------------------------- universe

def _eligible(sym: str) -> bool:
    if not sym.endswith("USDT"):
        return False
    base = sym[:-4]
    # 06/11: Binance lists ~105 tokenized US stocks / ETFs as <TICKER>B (AAPLB, NVDAB, SOXLB, TQQQB, MUB...): they only
    # move in US market hours and polluted the top 50 (Kronos "forecast" +27 % on SOXLB). Any base ending in B is a
    # stock token except the real cryptos of CRYPTO_B (checked on exchangeInfo 06/11).
    if base.endswith("B") and len(base) > 2 and base not in CRYPTO_B:
        return False
    return (base not in STABLES and base not in WRAPPED
            and not base.endswith(("UP", "DOWN", "BULL", "BEAR")) and base.isascii() and base.isalnum())


def universe(n: int = N_MARKETS, max_age_s: float = 6 * 3600) -> list[str]:
    """Top n Binance USDT spot markets by 24 h quote volume (status TRADING)."""
    with _LOCK:
        if _universe["symbols"] and time.time() - _universe["at"] < max_age_s:
            return _universe["symbols"][:n]
    try:
        info = requests.get("https://api.binance.com/api/v3/exchangeInfo", params={"permissions": "SPOT"},
                            timeout=20).json()
        trading = {s["symbol"] for s in info.get("symbols", []) if s.get("status") == "TRADING"}
        tks = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=20).json()
        vol = sorted(((t["symbol"], float(t["quoteVolume"])) for t in tks
                      if isinstance(t, dict) and t.get("symbol") in trading and _eligible(t["symbol"])),
                     key=lambda sv: -sv[1])
        syms = [s for s, _ in vol]
        if len(syms) >= 10:
            with _LOCK:
                _universe.update(at=time.time(), symbols=syms)
    except Exception:
        pass
    with _LOCK:
        return (_universe["symbols"] or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"])[:n]


# ---------------------------------------------------------------- scoring

def _read(name: str) -> dict:
    try:
        return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def score_market(sym: str, chart: dict, kronos: dict | None, kronos_trusted: bool, radar: dict | None) -> dict:
    """Composite -1..+1 from the specialists, with the decision and its reasons."""
    h1, h4, m5 = chart.get("1h") or {}, chart.get("4h") or {}, chart.get("5m") or {}
    price = m5.get("price") or h1.get("price")
    reasons, score = [], 0.0
    if "score" in h4:
        score += 0.45 * h4["score"]
        reasons.append(f"4 h {h4.get('trend')} ({h4['score']:+.2f})")
    if "score" in h1:
        score += 0.30 * h1["score"]
        reasons.append(f"1 h {h1.get('trend')} ({h1['score']:+.2f})")
    for tf, a in (("1h", h1), ("4h", h4)):
        if a.get("pattern"):
            reasons.append(f"{tf} {a['pattern'].replace('_', ' ')}")
    k_term = 0.0
    if kronos and h1.get("atr_pct"):          # no ATR = no scale: Kronos is left out rather than guessed
        r4 = (kronos.get("pred_ret_pct") or {}).get("4", 0.0)
        atr = h1["atr_pct"]
        k_term = max(-1.0, min(1.0, r4 / (2 * atr))) * kronos.get("agree", 0.0)
        reasons.append(f"Kronos 4 h {r4:+.2f} % accord {kronos.get('agree', 0):.0%}"
                       + ("" if kronos_trusted else " (mesure)"))
        if kronos_trusted:
            score += 0.25 * k_term
    if radar:
        f8 = radar.get("funding_8h_pct") or 0.0
        if f8 >= 0.03:
            score -= 0.10
            reasons.append(f"funding {f8:+.3f} % (acheteurs entasses)")
        elif f8 <= -0.03:
            score += 0.10
            reasons.append(f"funding {f8:+.3f} % (vendeurs entasses)")
    score = max(-1.0, min(1.0, score))
    t4 = h4.get("trend")
    if score >= ENTER and t4 != "down":
        decision = "LONG"
    elif score <= -ENTER and t4 != "up":
        decision = "SHORT"
    else:
        decision = "WAIT"
    return {"symbol": sym, "price": price, "score": round(score, 3), "decision": decision,
            "confidence": round(min(1.0, abs(score) / 0.7), 2) if decision != "WAIT" else 0.0,
            "kronos_term": round(k_term, 3), "atr_1h_pct": h1.get("atr_pct"), "reasons": reasons}


# ---------------------------------------------------------------- shadow book

def _load_book() -> dict:
    b = _read(BOOK.name)
    return {"open": b.get("open", {}), "closed": b.get("closed", [])}


def _close(pos: dict, price: float, why: str, now: float) -> dict:
    side = 1 if pos["side"] == "LONG" else -1
    net = side * (price / pos["entry"] - 1) * 100 - COST_PCT
    return {**pos, "exit": price, "closed": now, "why": why, "net_pct": round(net, 3)}


def update_book(book: dict, rows: list[dict], now: float) -> dict:
    by = {r["symbol"]: r for r in rows}
    for sym, pos in list(book["open"].items()):
        r = by.get(sym)
        price = (r or {}).get("price")
        if not price:
            if now - pos["opened"] > MAX_HOLD_S:            # market left the universe: close at last known price
                book["closed"].append(_close(pos, pos.get("last", pos["entry"]), "sorti_univers", now))
                del book["open"][sym]
            continue
        pos["last"] = price
        up = price >= pos["target"] if pos["side"] == "LONG" else price <= pos["target"]
        down = price <= pos["stop"] if pos["side"] == "LONG" else price >= pos["stop"]
        flip = r["decision"] not in ("WAIT", pos["side"])
        why = "objectif" if up else "stop" if down else "inverse" if flip else "duree" if now - pos["opened"] > MAX_HOLD_S else ""
        if why:
            book["closed"].append(_close(pos, price, why, now))
            del book["open"][sym]
    for r in rows:
        if r["decision"] == "WAIT" or r["symbol"] in book["open"] or not r["price"] or not r.get("atr_1h_pct"):
            continue                                       # no measured ATR = no stop / target = no trade
        atr = r["atr_1h_pct"] / 100
        side = 1 if r["decision"] == "LONG" else -1
        book["open"][r["symbol"]] = {"symbol": r["symbol"], "side": r["decision"], "entry": r["price"],
                                     "stop": r["price"] * (1 - side * 1.5 * atr),
                                     "target": r["price"] * (1 + side * 3.0 * atr),
                                     "opened": now, "confidence": r["confidence"], "score": r["score"]}
    book["closed"] = book["closed"][-2000:]
    return book


def book_stats(book: dict) -> dict:
    c = book["closed"]
    if not c:
        return {"closed": 0, "open": len(book["open"]), "proven": False}
    nets = [x["net_pct"] for x in c]
    avg = sum(nets) / len(nets)
    return {"closed": len(c), "open": len(book["open"]), "win_rate": round(sum(n > 0 for n in nets) / len(nets), 3),
            "avg_net_pct": round(avg, 4), "sum_net_pct": round(sum(nets), 2),
            "long": sum(x["side"] == "LONG" for x in c), "short": sum(x["side"] == "SHORT" for x in c),
            "proven": len(c) >= PROVEN_N and avg > 0}


# ---------------------------------------------------------------- run

def tick() -> dict:
    now = time.time()
    syms = universe()
    ca = _read("chart_analysis.json")
    # stale chart data (ChartAnalyst dead) = no price = no decision and no shadow trade (06/11 audit)
    charts = ca.get("symbols", {}) if now - (ca.get("at") or 0) < 3 * 300 else {}
    kr = _read("kronos.json")
    if kr.get("at") and now - kr["at"] > 3 * 3600:
        kr = {**kr, "markets": {}, "trusted": False}
    k_markets, k_ok = kr.get("markets", {}), bool(kr.get("trusted"))
    rd = _read("radar.json")
    radar = ({f"{c.get('coin')}USDT": c for c in rd.get("coins", []) if c.get("coin")}
             if now - (rd.get("at") or 0) < 3 * 300 else {})
    rows = [score_market(s, charts.get(s) or {}, k_markets.get(s), k_ok, radar.get(s)) for s in syms]
    rows.sort(key=lambda r: -abs(r["score"]))
    old = {m["symbol"]: m["decision"] for m in _read(OUT.name).get("markets", [])}
    book = update_book(_load_book(), rows, now)
    bs = book_stats(book)
    data = {"at": now, "universe_n": len(syms), "analysed_n": sum(1 for r in rows if r["price"]),
            "chart_age_s": int(now - ca["at"]) if ca.get("at") else None,
            "decisions": {d: sum(r["decision"] == d for r in rows) for d in ("LONG", "SHORT", "WAIT")},
            "kronos": {"status": kr.get("status", "absent"), "trusted": k_ok, "at": kr.get("at")},
            "book": bs, "open": list(book["open"].values()), "last_closed": book["closed"][-30:], "markets": rows}
    with _LOCK:
        for path, obj in ((BOOK, book), (OUT, data)):
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(obj), encoding="utf-8")
            tmp.replace(path)
    _post([r for r in rows if r["decision"] != "WAIT" and old.get(r["symbol"]) != r["decision"]], bs["proven"])
    return data


def _post(changed: list[dict], proven: bool, top: int = 5) -> None:
    try:
        import signals
        for r in sorted(changed, key=lambda r: -r["confidence"])[:top]:
            if r["confidence"] < 0.6:
                continue
            signals.post("MarketDesk", r["symbol"], "setup", r["confidence"] if proven else 0.0, 900, to="FillEngine",
                         reason=f"{r['decision']} score {r['score']:+.2f} : " + ", ".join(r["reasons"][:3])
                                + ("" if proven else " - mesure"), side=r["decision"])
    except Exception:
        pass


def candidates(min_conf: float = 0.6, side: str | None = None, proven_only: bool = True,
               max_age_s: float = 900) -> list[dict]:
    """Current LONG / SHORT setups for the FillEngine, best first. Empty until the shadow book is proven."""
    d = _read(OUT.name)
    if not d or time.time() - d.get("at", 0) > max_age_s:
        return []
    if proven_only and not (d.get("book") or {}).get("proven"):
        return []
    return [{"symbol": r["symbol"], "side": r["decision"], "confidence": r["confidence"], "score": r["score"],
             "reasons": r["reasons"]} for r in d.get("markets", [])
            if r["decision"] != "WAIT" and r["confidence"] >= min_conf and (side is None or r["decision"] == side)]


def start(every_s: int = 300) -> None:
    global _started
    if _started or not int(os.environ.get("MARKET_DESK_ENABLED", 1)):
        return
    _started = True
    from agent_events import log_event

    def loop():
        time.sleep(150)                      # after the first ChartAnalyst round
        while True:
            try:
                d = tick()
                b = d["book"]
                log_event("MarketDesk", "analyse", "INFO", "",
                          f"{d['analysed_n']}/{d['universe_n']} marches : {d['decisions']['LONG']} LONG, "
                          f"{d['decisions']['SHORT']} SHORT ; carnet fictif {b.get('closed', 0)} trades, "
                          f"moyenne {b.get('avg_net_pct', '-')} % net" + (" - PROUVE" if b.get("proven") else ""),
                          result=d["decisions"])
            except Exception as e:
                log_event("MarketDesk", "analyse", "INFO", "", f"erreur interne {type(e).__name__}: {str(e)[:200]}")
            time.sleep(every_s)

    threading.Thread(target=loop, daemon=True, name="market_desk").start()
