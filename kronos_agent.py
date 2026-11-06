"""KronosAgent (06/11, user request): forecasts the next 1 h candles of the 50 desk markets with Kronos.

Kronos (github.com/shiyu-coder/Kronos, MIT, AAAI 2026) is a foundation model trained on candlesticks of 45 exchanges.
Its code is vendored in vendor/kronos/model (unchanged); the weights come from Hugging Face (NeoQuasar/Kronos-small,
24.7 M parameters, runs on CPU) and are cached by huggingface_hub on first use.

Every hour (on closed candles only, no look-ahead):
  1. last LOOKBACK 1 h candles of every market (Binance public klines, OHLCV + quote volume);
  2. PATHS independent forecast paths of PRED_LEN candles for all markets in one batch per path;
  3. per market: mean predicted return at +1 h / +4 h / +12 h, predicted high / low, and the share of paths that
     agree on the 4 h direction (a measure of the model's own certainty);
  4. scoring of the earlier forecasts against what really happened: hit rate, net edge after costs and rank IC
     (does the model rank the 50 markets in the right order?).

Measurement first (house rule): independent tests found ~50 % direction accuracy on forex / CFDs and crypto, so the
agent's signals carry confidence 0 until its own track record on OUR markets passes the bar in trusted():
>= 500 scored 4 h forecasts over >= 48 runs, hit rate >= 53 %, positive edge after costs, rank IC > 0.02.

    run_once(symbols) -> dict          one forecast round, writes DATA_DIR/kronos.json
    load() -> dict                     last saved state (for market_desk and the dashboard)
    start(symbols_fn, every_s=3600)    background thread; never raises into trading code
If torch is not installed the agent writes {"status": "indisponible"} and stops: the bot keeps running.
"""
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "kronos.json"
TRACK = DATA_DIR / "kronos_track.json"
VENDOR = Path(__file__).resolve().parent / "vendor" / "kronos"
MODEL = os.environ.get("KRONOS_MODEL", "NeoQuasar/Kronos-small")
TOKENIZER = os.environ.get("KRONOS_TOKENIZER", "NeoQuasar/Kronos-Tokenizer-base")
TF, TF_MS = "1h", 3_600_000
LOOKBACK = int(os.environ.get("KRONOS_LOOKBACK", 400))       # <= 512, the context of Kronos-small
PRED_LEN = int(os.environ.get("KRONOS_PRED_LEN", 12))
PATHS = int(os.environ.get("KRONOS_PATHS", 3))   # 06/11: 3 paths so A + B (100 markets) fits in ~30 min
HORIZONS = (1, 4, 12)
COST_PCT = float(os.environ.get("KRONOS_COST_PCT", 0.10))    # round trip fees + slippage, in % of the position
TRACK_DAYS = 30
BINANCE = "https://api.binance.com"
_LOCK = threading.Lock()
_predictor = None
_started = False


# ---------------------------------------------------------------- data

def candles(symbol: str, limit: int = LOOKBACK) -> list[tuple]:
    """(open_ms, open, high, low, close, volume, quote_volume), oldest first, closed candles only."""
    r = requests.get(f"{BINANCE}/api/v3/klines", params={"symbol": symbol, "interval": TF, "limit": limit + 1},
                     timeout=20)
    r.raise_for_status()
    rows = [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), float(k[7]))
            for k in r.json()]
    return rows[:-1]


def fetch_all(symbols: list[str], limit: int = LOOKBACK) -> dict[str, list[tuple]]:
    """Candles of every market in parallel (8 at a time, ~2 weight each: far under Binance's 6000/min)."""
    def one(s):
        try:
            return s, candles(s, limit)
        except Exception:
            return s, []
    with ThreadPoolExecutor(8) as ex:
        return {s: c for s, c in ex.map(one, symbols) if len(c) >= limit}


# ---------------------------------------------------------------- model

def available() -> bool:
    import importlib.util
    return all(importlib.util.find_spec(m) for m in ("torch", "einops", "pandas", "huggingface_hub"))         and (VENDOR / "model" / "kronos.py").exists()


def predictor():
    global _predictor
    if _predictor is None:
        os.environ.setdefault("HF_HOME", str(DATA_DIR / "hf_cache"))   # weights kept on the data volume
        import torch
        torch.set_num_threads(int(os.environ.get("KRONOS_THREADS", 2)))   # leave CPU for the trading threads
        if str(VENDOR) not in sys.path:
            sys.path.insert(0, str(VENDOR))
        from model import Kronos, KronosPredictor, KronosTokenizer
        tok = KronosTokenizer.from_pretrained(TOKENIZER)
        mdl = Kronos.from_pretrained(MODEL)
        tok.eval()
        mdl.eval()
        _predictor = KronosPredictor(mdl, tok, device="cpu", max_context=512)
    return _predictor


def forecast(series: dict[str, list[tuple]], paths: int = PATHS, pred_len: int = PRED_LEN) -> dict[str, dict]:
    """PATHS sampled futures per market, one batch per path; returns per-market summary."""
    import pandas as pd
    syms = list(series)
    if not syms:
        return {}
    dfs, xts, yts = [], [], []
    for s in syms:
        c = series[s]
        dfs.append(pd.DataFrame([x[1:] for x in c], columns=["open", "high", "low", "close", "volume", "amount"]))
        xts.append(pd.Series(pd.to_datetime([x[0] for x in c], unit="ms")))
        last = c[-1][0]
        yts.append(pd.Series(pd.to_datetime([last + TF_MS * (k + 1) for k in range(pred_len)], unit="ms")))
    p = predictor()
    closes = np.zeros((paths, len(syms), pred_len))
    highs, lows = np.zeros_like(closes), np.zeros_like(closes)
    for i in range(paths):
        preds = p.predict_batch(dfs, xts, yts, pred_len=pred_len, T=1.0, top_p=0.9, sample_count=1, verbose=False)
        for j, df in enumerate(preds):
            closes[i, j], highs[i, j], lows[i, j] = df["close"].values, df["high"].values, df["low"].values
    out = {}
    for j, s in enumerate(syms):
        c0 = series[s][-1][4]
        rets = closes[:, j, :] / c0 - 1                                    # (paths, pred_len)
        mean = rets.mean(0)
        r4 = rets[:, min(3, pred_len - 1)]
        sign = np.sign(r4.mean()) or 1.0
        out[s] = {"t0": series[s][-1][0], "price": c0,
                  "pred_ret_pct": {str(h): round(float(mean[h - 1]) * 100, 3) for h in HORIZONS if h <= pred_len},
                  "agree": round(float((np.sign(r4) == sign).mean()), 2),
                  "spread_4h_pct": round(float(r4.std()) * 100, 3),
                  "pred_high_pct": round(float(highs[:, j, :].mean(0).max() / c0 - 1) * 100, 3),
                  "pred_low_pct": round(float(lows[:, j, :].mean(0).min() / c0 - 1) * 100, 3),
                  "direction": "up" if mean[min(3, pred_len - 1)] > 0 else "down"}
    return out


# ---------------------------------------------------------------- self-scoring

def _rank(x: np.ndarray) -> np.ndarray:
    r = np.empty(len(x))
    r[np.argsort(x, kind="stable")] = np.arange(len(x))
    return r


def spearman(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 5 or a.std() == 0 or b.std() == 0:
        return 0.0
    ra, rb = _rank(a), _rank(b)
    return float(np.corrcoef(ra, rb)[0, 1])


def score_track(track: list[dict], series: dict[str, list[tuple]]) -> list[dict]:
    """Fill the realized return of past forecasts from the candles just fetched (no extra request)."""
    closes = {s: {x[0]: x[4] for x in c} for s, c in series.items()}
    keep_after = (time.time() - TRACK_DAYS * 86400) * 1000
    out = []
    for rec in track:
        if rec["t0"] < keep_after:
            continue
        cs = closes.get(rec["sym"], {})
        for h in HORIZONS:
            k = str(h)
            if k in rec.get("real", {}) or k not in rec["pred"]:
                continue
            c_h = cs.get(rec["t0"] + TF_MS * h)        # close of the h-th candle after the forecast candle
            if c_h:
                rec.setdefault("real", {})[k] = round((c_h / rec["c0"] - 1) * 100, 4)
        out.append(rec)
    return out


def stats(track: list[dict]) -> dict:
    res = {}
    for h in HORIZONS:
        k = str(h)
        rows = [r for r in track if k in r.get("real", {}) and k in r["pred"]]
        if not rows:
            res[k] = {"n": 0}
            continue
        p = np.array([r["pred"][k] for r in rows])
        y = np.array([r["real"][k] for r in rows])
        hit = float((np.sign(p) == np.sign(y)).mean())
        edge = float((np.sign(p) * y).mean() - COST_PCT)            # trade every forecast in its direction, net
        runs = {}
        for r in rows:
            runs.setdefault(r["t0"], []).append((r["pred"][k], r["real"][k]))
        ics = [spearman([a for a, _ in v], [b for _, b in v]) for v in runs.values() if len(v) >= 10]
        res[k] = {"n": len(rows), "runs": len(runs), "hit": round(hit, 3), "edge_net_pct": round(edge, 4),
                  "ic": round(float(np.mean(ics)), 4) if ics else None,
                  "ic_t": round(float(np.mean(ics) / (np.std(ics) / np.sqrt(len(ics)))), 2)
                  if len(ics) >= 3 and np.std(ics) > 0 else None}
    return res


def trusted(st: dict) -> bool:
    s = st.get("4") or {}
    return (s.get("n", 0) >= 500 and s.get("runs", 0) >= 48 and s.get("hit", 0) >= 0.53
            and s.get("edge_net_pct", -1) > 0 and (s.get("ic") or 0) > 0.02)


# ---------------------------------------------------------------- run

def load() -> dict:
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def run_once(symbols: list[str], universe_of: dict | None = None) -> dict:
    t = time.time()
    series = fetch_all(symbols)
    try:
        track = json.loads(TRACK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        track = []
    track = score_track(track, series)
    fc = forecast(series)
    seen = {(r["sym"], r["t0"]) for r in track}
    for s, f in fc.items():
        if (s, f["t0"]) not in seen:          # a second run in the same hour must not count twice
            track.append({"sym": s, "t0": f["t0"], "c0": f["price"], "pred": f["pred_ret_pct"],
                          "agree": f["agree"]})
    st = stats(track)
    ok = trusted(st)
    # 06/11 two universes: B runs every other hour, so keep the previous forecasts that are < 3 h old
    prev = load().get("markets") or {}
    now_ms = time.time() * 1000
    merged = {k: v for k, v in prev.items() if k not in fc and now_ms - v.get("t0", 0) < 3 * 3600 * 1000}
    merged.update(fc)
    if universe_of:
        for k, v in merged.items():
            v["universe"] = universe_of.get(k, v.get("universe"))
    data = {"at": time.time(), "status": "ok", "model": MODEL, "took_s": round(time.time() - t, 1),
            "markets_n": len(merged), "this_round_n": len(fc), "paths": PATHS, "horizon_h": PRED_LEN,
            "trusted": ok, "stats": st, "markets": merged}
    with _LOCK:
        _save(TRACK, track)
        _save(OUT, data)
    _post(fc, ok)
    return data


def _post(fc: dict, ok: bool, top: int = 5) -> None:
    """Strongest agreed forecasts as SIGNAL messages; confidence 0 while the agent is not proven."""
    try:
        import signals
        best = sorted(((s, f) for s, f in fc.items() if f["agree"] >= 0.8),
                      key=lambda sf: -abs(sf[1]["pred_ret_pct"].get("4", 0)) * sf[1]["agree"])[:top]
        for s, f in best:
            r4 = f["pred_ret_pct"].get("4", 0)
            conf = round(min(1.0, abs(r4) / 2) * f["agree"], 2) if ok else 0.0
            signals.post("Kronos", s, "forecast", conf, 3600, to="MarketDesk",
                         reason=f"prevision 4 h {r4:+.2f} % (accord {f['agree']:.0%})"
                                + ("" if ok else " - mesure, pas encore fiable"),
                         horizon_h=4, pred_ret_pct=r4, agree=f["agree"])
    except Exception:
        pass


def start(symbols_fn=None, every_s: int = 3600) -> None:
    """symbols_fn() -> list of markets (default: the market desk universe)."""
    global _started
    if _started or not int(os.environ.get("KRONOS_ENABLED", 1)):
        return
    _started = True
    from agent_events import log_event

    def loop():
        time.sleep(120)
        if not available():
            _save(OUT, {"at": time.time(), "status": "indisponible", "why": "torch / einops / vendor/kronos absent"})
            log_event("Kronos", "analyse", "INFO", "", "Kronos indisponible : torch ou einops non installes")
            return
        while True:
            try:
                uni = None
                if symbols_fn:
                    syms = symbols_fn()
                else:
                    # 06/11 two universes: A every hour, B every other hour (100 markets at once would take ~40 min
                    # on the 4-core VPS); B forecasts stay valid 3 h in between
                    import market_desk
                    a = market_desk.universe("A")
                    b = market_desk.universe("B") if time.gmtime().tm_hour % 2 == 0 else []
                    syms = a + b
                    uni = {**{s: "A" for s in a}, **{s: "B" for s in b}}
                d = run_once(syms, uni)
                s4 = d["stats"].get("4", {})
                log_event("Kronos", "analyse", "INFO", "",
                          f"{d['markets_n']} marches prevus en {d['took_s']} s ; 4 h : {s4.get('n', 0)} notes, "
                          f"reussite {s4.get('hit', '-')}, IC {s4.get('ic', '-')}"
                          + (" - FIABLE" if d["trusted"] else " - mesure"), result=s4)
            except Exception as e:
                log_event("Kronos", "analyse", "INFO", "", f"erreur interne {type(e).__name__}: {str(e)[:200]}")
            # next round just after the next hourly close
            time.sleep(max(300, every_s - (time.time() % 3600) + 90))

    threading.Thread(target=loop, daemon=True, name="kronos").start()
