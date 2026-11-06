"""ChartAnalyst (spec C of ~/.claude/plans/trading-moteur-gestion-et-infos.md): candle analysis
without AI, for the symbols we hold or consider.

Data: Binance candles 5m / 1h / 4h (public API) for crypto, Alpaca 15m / 1d for US stocks.
Per symbol and timeframe: trend (MA20/MA50 position and slope), RSI 14, MACD histogram, ATR 14 in %
of price, supports / resistances (swing pivots clustered at 0.5 x ATR, 3 most touched each side),
distance to the nearest ones in ATR, rule-based patterns (breakout with volume >= 1.5 x average,
support retest, higher highs & lows / lower highs & lows, volatility squeeze = ATR at its 50-bar
low) and a score -1..+1.

Output: DATA_DIR/chart_analysis.json {"at", "symbols": {sym: {tf: {...}}}} and a SIGNAL line in
agent_events when a held symbol shows a new pattern. Helpers for the other agents: trend_4h(),
near_resistance(), nearest_support(), first_resistance().
"""
import json
import os
import threading
import time
from pathlib import Path

import requests

from agent_events import log_event

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "chart_analysis.json"
BINANCE = "https://api.binance.com"
CRYPTO_TF = ("5m", "1h", "4h")
STOCK_TF = ("15m", "1d")
_LOCK = threading.Lock()


# ---------------------------------------------------------------- data

def candles(symbol: str, tf: str, limit: int = 200) -> list[tuple]:
    """(open_ms, open, high, low, close, volume), oldest first, closed candles only."""
    if symbol.endswith("USDT"):
        r = requests.get(f"{BINANCE}/api/v3/klines", params={"symbol": symbol, "interval": tf, "limit": limit + 1},
                         timeout=15)
        r.raise_for_status()
        rows = [(k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])) for k in r.json()]
        return rows[:-1]
    import alpaca_broker  # stocks
    return alpaca_broker.get_recent_bars(symbol, tf, 10 if tf == "15m" else 300)[-limit:]


# ---------------------------------------------------------------- indicators

def sma(xs: list[float], n: int) -> list[float | None]:
    out, s = [], 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def ema(xs: list[float], n: int) -> list[float]:
    k, out = 2 / (n + 1), []
    for x in xs:
        out.append(x if not out else out[-1] + k * (x - out[-1]))
    return out


def rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) <= n:
        return None
    gains = [max(0.0, b - a) for a, b in zip(closes, closes[1:])]
    losses = [max(0.0, a - b) for a, b in zip(closes, closes[1:])]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):  # Wilder smoothing
        ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + l) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def atr_series(c: list[tuple], n: int = 14) -> list[float]:
    trs = [c[0][2] - c[0][3]] + [max(h - l, abs(h - pc), abs(l - pc))
                                 for (_, _, h, l, _, _), (_, _, _, _, pc, _) in zip(c[1:], c)]
    out = []
    for i, tr in enumerate(trs):
        out.append(tr if i == 0 else (out[-1] * (n - 1) + tr) / n)
    return out


def levels(c: list[tuple], atr: float, price: float, k: int = 3) -> tuple[list[float], list[float]]:
    """Swing pivots (2 bars each side), clustered at 0.5 x ATR, 3 most touched below / above price."""
    piv = []
    for i in range(2, len(c) - 2):
        h, l = c[i][2], c[i][3]
        if h >= max(x[2] for x in c[i - 2:i + 3]):
            piv.append(h)
        if l <= min(x[3] for x in c[i - 2:i + 3]):
            piv.append(l)
    clusters: list[list[float]] = []
    for p in sorted(piv):
        if clusters and p - clusters[-1][-1] <= 0.5 * atr:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    lv = [(sum(g) / len(g), len(g)) for g in clusters]
    sup = sorted([x for x in lv if x[0] < price], key=lambda x: -x[1])[:k]
    res = sorted([x for x in lv if x[0] > price], key=lambda x: -x[1])[:k]
    return sorted((round(x[0], 8) for x in sup), reverse=True), sorted(round(x[0], 8) for x in res)


def analyse(c: list[tuple]) -> dict:
    closes = [x[4] for x in c]
    price = closes[-1]
    ma20, ma50 = sma(closes, 20), sma(closes, 50)
    m20, m50 = ma20[-1], ma50[-1]
    slope20 = (m20 - ma20[-6]) / ma20[-6] if m20 and ma20[-6] else 0.0
    slope50 = (m50 - ma50[-6]) / ma50[-6] if m50 and ma50[-6] else 0.0
    if m20 and m50 and price > m20 > m50 and slope20 > 0:
        trend = "up"
    elif m20 and m50 and price < m20 < m50 and slope20 < 0:
        trend = "down"
    else:
        trend = "flat"
    macd_line = [a - b for a, b in zip(ema(closes, 12), ema(closes, 26))]
    hist = macd_line[-1] - ema(macd_line, 9)[-1]
    atrs = atr_series(c)
    atr = atrs[-1]
    r = rsi(closes)
    sup, res = levels(c[:-1], atr, price)
    # patterns, by rules
    pattern = ""
    vol_avg = sum(x[5] for x in c[-21:-1]) / 20 if len(c) > 21 else 0
    prev_res = [lv for lv in levels(c[:-2], atrs[-2], c[-2][4])[1]]
    if prev_res and closes[-2] <= prev_res[0] < price and vol_avg and c[-1][5] >= 1.5 * vol_avg:
        pattern = "cassure_resistance_volume"
    elif sup and c[-1][3] <= sup[0] + 0.25 * atr and price > sup[0]:
        pattern = "retest_support"
    elif len(atrs) >= 50 and atr <= min(atrs[-50:]) * 1.0001:
        pattern = "compression_volatilite"
    else:
        highs = [c[i][2] for i in range(2, len(c) - 2) if c[i][2] >= max(x[2] for x in c[i - 2:i + 3])][-2:]
        lows = [c[i][3] for i in range(2, len(c) - 2) if c[i][3] <= min(x[3] for x in c[i - 2:i + 3])][-2:]
        if len(highs) == 2 and len(lows) == 2:
            if highs[1] > highs[0] and lows[1] > lows[0]:
                pattern = "plus_hauts_plus_bas_croissants"
            elif highs[1] < highs[0] and lows[1] < lows[0]:
                pattern = "plus_hauts_plus_bas_decroissants"
    score = {"up": 0.4, "down": -0.4}.get(trend, 0.0) + (0.2 if hist > 0 else -0.2)
    if r is not None:
        score += -0.1 if r > 75 else 0.1 if r < 25 else 0.0
    score += {"cassure_resistance_volume": 0.3, "retest_support": 0.2, "plus_hauts_plus_bas_croissants": 0.2,
              "plus_hauts_plus_bas_decroissants": -0.3}.get(pattern, 0.0)
    return {"price": price, "trend": trend, "ma20": m20, "ma50": m50, "slope20": round(slope20, 5),
            "slope50": round(slope50, 5), "rsi": round(r, 1) if r is not None else None, "macd_hist": hist,
            "atr_pct": round(atr / price * 100, 3), "atr": atr, "supports": sup, "resistances": res,
            "dist_support_atr": round((price - sup[0]) / atr, 2) if sup and atr else None,
            "dist_resistance_atr": round((res[0] - price) / atr, 2) if res and atr else None,
            "pattern": pattern, "score": round(max(-1.0, min(1.0, score)), 2), "at": int(c[-1][0] / 1000)}


# ---------------------------------------------------------------- store and helpers

def load() -> dict:
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"at": 0, "symbols": {}}


def run(symbols: list[str], held: set[str] | None = None) -> dict:
    """Analyse every symbol on its timeframes, save, log new patterns on held symbols."""
    held = held or set()
    old = load().get("symbols", {})

    def one(s: str) -> tuple[str, dict]:
        res = {}
        for tf in (CRYPTO_TF if s.endswith("USDT") else STOCK_TF):
            try:
                c = candles(s, tf)
                if len(c) >= 60:
                    res[tf] = analyse(c)
            except Exception as e:  # one symbol never stops the others
                res[tf] = {"error": f"{type(e).__name__}: {str(e)[:80]}"}
        return s, res

    # 06/11: the market desk asks for ~50 markets x 3 timeframes; 8 in parallel keeps a round under ~20 s
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as ex:
        out = dict(ex.map(one, sorted(set(symbols))))
    for s in out:
        for tf in out[s]:
            p = out[s].get(tf, {}).get("pattern")
            # SIGNAL only on 1h / 4h / 1d: 5m and 15m patterns change every run and would flood the journal
            if tf in ("1h", "4h", "1d") and s in held and p and p != old.get(s, {}).get(tf, {}).get("pattern"):
                a = out[s][tf]
                log_event("ChartAnalyst", "graphiques", "SIGNAL", s, f"{tf} : {p.replace('_', ' ')}",
                          score=a["score"], result="haussier" if a["score"] > 0.15 else "baissier" if a["score"] < -0.15 else "neutre",
                          trend=a["trend"], timeframe=tf, rsi=a["rsi"], atr_pct=a["atr_pct"])
    data = {"at": time.time(), "symbols": out}
    with _LOCK:
        tmp = OUT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(OUT)
    return data


def ensure(symbol: str, max_age_s: float = 900) -> dict:
    """Analysis of one symbol, computed on demand when missing or older than max_age_s
    (candidates that are not held yet), merged into chart_analysis.json."""
    data = load()
    cur = data.get("symbols", {}).get(symbol)
    analysed_at = max(float(data.get("ensured", {}).get(symbol, 0)), float(data.get("at", 0)) if cur else 0.0)
    if cur and time.time() - analysed_at < max_age_s:
        return cur
    tfs = CRYPTO_TF if symbol.endswith("USDT") else STOCK_TF
    res = {}
    for tf in tfs:
        try:
            c = candles(symbol, tf)
            if len(c) >= 60:
                res[tf] = analyse(c)
        except Exception as e:
            res[tf] = {"error": f"{type(e).__name__}: {str(e)[:80]}"}
    with _LOCK:
        data = load()
        data.setdefault("symbols", {})[symbol] = res
        data.setdefault("ensured", {})[symbol] = time.time()
        tmp = OUT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(OUT)
    return res


def _tf(symbol: str, tf: str) -> dict:
    return load().get("symbols", {}).get(symbol, {}).get(tf, {}) or {}


def trend_4h(symbol: str) -> str:
    """'up' | 'down' | 'flat' | '' (unknown). Stocks use the daily chart."""
    return _tf(symbol, "4h" if symbol.endswith("USDT") else "1d").get("trend", "")


def near_resistance(symbol: str, tf: str = "4h", within_atr: float = 0.5) -> bool:
    d = _tf(symbol, tf).get("dist_resistance_atr")
    return d is not None and d < within_atr


def nearest_support(symbol: str, tf: str = "1h") -> tuple[float | None, float | None]:
    """(support level, ATR) on that timeframe."""
    a = _tf(symbol, tf)
    return ((a.get("supports") or [None])[0], a.get("atr"))


def first_resistance(symbol: str, tf: str = "1h") -> float | None:
    return (_tf(symbol, tf).get("resistances") or [None])[0]


# ---------------------------------------------------------------- loop

_started = False


def held_symbols() -> set[str]:
    """Symbols of the open positions of the live portfolios (crypto copy + US stocks)."""
    out = set()
    for name in ("hl_copy_ledger.json", "alpaca_stocks_ledger.json", "meme_sim.json"):
        try:
            led = json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for k, p in (led.get("positions") or {}).items():
            out.add(p.get("symbol") or k)
    return {s for s in out if s and ":" not in s}


def default_symbols() -> tuple[list[str], set[str]]:
    held = held_symbols()
    try:  # 06/11: plus the 50 markets of the market desk
        import market_desk
        desk = set(market_desk.universe())
    except Exception:
        desk = set()
    return sorted(held | desk | {"BTCUSDT", "ETHUSDT"}), held


def start(symbols_fn=None, every_s: int = 300) -> None:
    """symbols_fn() -> (symbols to analyse, held symbols); default: open positions + BTC/ETH."""
    symbols_fn = symbols_fn or default_symbols
    global _started
    if _started or not int(os.environ.get("CHART_ANALYST_ENABLED", 1)):
        return
    _started = True

    def loop():
        time.sleep(90)
        while True:
            try:
                syms, held = symbols_fn()
                run(syms, held)
            except Exception as e:
                log_event("ChartAnalyst", "graphiques", "INFO", "", f"erreur interne {type(e).__name__}: {e}")
            time.sleep(every_s)

    threading.Thread(target=loop, daemon=True, name="chart_analyst").start()
