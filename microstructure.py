"""Microstructure agent for the 1-5 min book (Desktop, 06/11 research round 2). Measurement first, never raises.

Three signals the scalper does not have yet, each from published work / open-source practice:

1. VPIN (last 5 of 30 equal-volume buckets over 4 h of 1-min bars, bulk taker-buy classification), flow toxicity (Easley, Lopez de Prado, O'Hara; Easley et al. 2024 show it keeps predictive power in
   crypto): share of one-sided volume in equal-volume buckets. High VPIN = informed one-way flow -> fading it
   (mean reversion) is dangerous, riding it (breakout) is fine.
2. Variance ratio VR(5) on 1-min returns (Lo-MacKinlay; DeepAlpha uses Hurst for the same job): VR > 1 trending,
   VR < 1 mean-reverting. Routes the scalper to the strategy that fits the current tape instead of firing both.
3. Futures stress from Binance USDT-M public data: funding, open-interest change over 1 h and taker buy/sell
   ratio. A fast OI drop with a big price move = a liquidation cascade in progress (Hyblock 2026 framework uses
   the same price-displacement + liquidation pairing) -> no counter-trend scalp, no new position against it.

    toxicity(symbol)      -> {"vpin", "buckets"} | None
    variance_ratio(symbol)-> float | None
    scalp_mode(symbol)    -> ("breakout" | "mean_reversion" | "any" | "skip", why)   fail-open "any"
    cascade_risk(symbol)  -> {"cascade": bool, "side": "down"/"up"/None, "oi_chg_1h", "px_chg_1h", "funding", ...} | None

Every scalp_mode / cascade_risk call is appended to DATA_DIR/microstructure_log.jsonl (filter_audit.py scores it).
"""
import json
import math
import os
import time
from pathlib import Path

import requests

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
SPOT, FUT = "https://api.binance.com", "https://fapi.binance.com"
VPIN_HIGH = float(os.environ.get("MS_VPIN_HIGH", 0.35))
VR_TREND, VR_MEANREV = float(os.environ.get("MS_VR_TREND", 1.15)), float(os.environ.get("MS_VR_MEANREV", 0.85))
CASCADE_OI, CASCADE_PX = float(os.environ.get("MS_CASCADE_OI", 0.03)), float(os.environ.get("MS_CASCADE_PX", 0.015))
CACHE_S = 15
_cache: dict[tuple, tuple[float, object]] = {}


def _cached(key, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def _log(row: dict) -> None:
    try:
        with open(DATA_DIR / "microstructure_log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def _klines(symbol: str, n: int = 240) -> list:
    return _cached(("kl", symbol), lambda: requests.get(f"{SPOT}/api/v3/klines", timeout=8,
                   params={"symbol": symbol, "interval": "1m", "limit": n + 1}).json())


def toxicity(symbol: str, n_buckets: int = 30) -> dict | None:
    """VPIN over the last 4 h: 1-min bars, taker-buy volume vs total, grouped into equal-volume buckets."""
    try:
        bars = [(float(x[5]), float(x[9])) for x in _klines(symbol)]   # volume, taker-buy base volume
        total = sum(v for v, _ in bars)
        if total <= 0:
            return None
        size, buckets, vol, buy = total / n_buckets, [], 0.0, 0.0
        for v, tb in bars:
            vol, buy = vol + v, buy + tb
            if vol >= size:
                buckets.append(abs(2 * buy - vol) / vol)
                vol = buy = 0.0
        if len(buckets) < 5:
            return None
        recent = sum(buckets[-5:]) / 5
        return {"vpin": round(sum(buckets) / len(buckets), 4), "vpin_recent": round(recent, 4),
                "buckets": len(buckets)}
    except Exception:
        return None


def variance_ratio(symbol: str, k: int = 5, n: int = 240) -> float | None:
    try:
        def fetch():
            c = [float(x[4]) for x in _klines(symbol, n)]
            r1 = [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
            rk = [math.log(c[i] / c[i - k]) for i in range(k, len(c))]
            m = sum(r1) / len(r1)
            v1 = sum((x - m) ** 2 for x in r1) / (len(r1) - 1)
            vk = sum((x - k * m) ** 2 for x in rk) / (len(rk) - 1)
            return round(vk / (k * v1), 4) if v1 > 0 else None
        return _cached(("vr", symbol), fetch)
    except Exception:
        return None


def scalp_mode(symbol: str) -> tuple[str, str]:
    """Which scalp strategy fits now: breakout in trending/toxic tape, mean reversion in a calm mean-reverting one."""
    tox, vr = toxicity(symbol), variance_ratio(symbol)
    vpin = tox["vpin_recent"] if tox else None
    if vr is None and vpin is None:
        mode, why = "any", "donnees indisponibles"
    elif vpin is not None and vpin >= VPIN_HIGH:
        mode, why = "breakout", f"flux toxique (VPIN {vpin:.2f}) : ne pas aller contre"
    elif vr is not None and vr >= VR_TREND:
        mode, why = "breakout", f"tendance (VR {vr:.2f})"
    elif vr is not None and vr <= VR_MEANREV:
        mode, why = "mean_reversion", f"retour a la moyenne (VR {vr:.2f})"
    else:
        mode, why = "skip", f"bruit sans structure (VR {vr}, VPIN {vpin})"
    _log({"ts": time.time(), "kind": "scalp_mode", "symbol": symbol, "mode": mode, "vr": vr, "vpin": vpin})
    return mode, why


def cascade_risk(symbol: str) -> dict | None:
    """Futures stress for a USDT-M perpetual (symbol like BTCUSDT)."""
    try:
        def fetch():
            oi = requests.get(f"{FUT}/futures/data/openInterestHist",
                              params={"symbol": symbol, "period": "5m", "limit": 13}, timeout=8).json()
            prem = requests.get(f"{FUT}/fapi/v1/premiumIndex", params={"symbol": symbol}, timeout=8).json()
            tk = requests.get(f"{FUT}/futures/data/takerlongshortRatio",
                              params={"symbol": symbol, "period": "5m", "limit": 12}, timeout=8).json()
            kl = requests.get(f"{FUT}/fapi/v1/klines", params={"symbol": symbol, "interval": "5m", "limit": 13},
                              timeout=8).json()
            oi_chg = float(oi[-1]["sumOpenInterest"]) / float(oi[0]["sumOpenInterest"]) - 1
            px_chg = float(kl[-1][4]) / float(kl[0][1]) - 1
            taker = sum(float(x["buySellRatio"]) for x in tk) / len(tk)
            cascade = oi_chg <= -CASCADE_OI and abs(px_chg) >= CASCADE_PX
            return {"cascade": cascade, "side": ("down" if px_chg < 0 else "up") if cascade else None,
                    "oi_chg_1h": round(oi_chg, 4), "px_chg_1h": round(px_chg, 4),
                    "funding": float(prem["lastFundingRate"]), "taker_buy_sell": round(taker, 3)}
        res = _cached(("casc", symbol), fetch)
        _log({"ts": time.time(), "kind": "cascade", "symbol": symbol, **res})
        return res
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    for s in sys.argv[1:] or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]:
        print(s, toxicity(s), variance_ratio(s), scalp_mode(s), cascade_risk(s))
