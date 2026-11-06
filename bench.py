"""Benchmark line for every book (user/Desktop 06/11): what the book did vs simply holding BTC over the same period.
A book that is up while BTC is down may only be short the market (direction, not a proven edge).
BOOK_START = the 05/11 reset (env BOOK_START_UTC). Never raises; "" when there is no data (never a made-up value).
"""
import os
import time
from datetime import datetime, timezone

import requests

BOOK_START_UTC = os.environ.get("BOOK_START_UTC", "2026-11-05T00:00:00+00:00")
_cache = {"at": 0.0, "pct": None}


def btc_since_start() -> float | None:
    """BTC change in % since BOOK_START (Binance 1 h close at that hour vs last price), cached 10 min."""
    if time.time() - _cache["at"] < 600:
        return _cache["pct"]
    pct = None
    try:
        t0 = int(datetime.fromisoformat(BOOK_START_UTC).timestamp() * 1000)
        k = requests.get("https://api.binance.com/api/v3/klines",
                         params={"symbol": "BTCUSDT", "interval": "1h", "startTime": t0, "limit": 1}, timeout=10).json()
        last = float(requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": "BTCUSDT"},
                                  timeout=10).json()["price"])
        if k and last:
            pct = (last / float(k[0][4]) - 1) * 100
    except Exception:
        pct = None
    _cache.update(at=time.time(), pct=pct)
    return pct


def line(book_return_pct: float | None) -> str:
    btc = btc_since_start()
    if book_return_pct is None or btc is None:
        return ""
    day = BOOK_START_UTC[8:10] + "/" + BOOK_START_UTC[5:7]
    diff = book_return_pct - btc
    return f"📏 vs BTC tenu depuis le {day} : portefeuille {book_return_pct:+.2f} % | BTC {btc:+.2f} % | écart {diff:+.2f} pts"
