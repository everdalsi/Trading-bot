"""Single RegimeAgent (inventory 2026-11-05: three live copies recomputed BTC vs its 50-day average in
trade_manager, short_sleeve and professor). One calculation, cached 1 h, read by all of them; each
keeps its own rule, unchanged:
- down        = close < MA50 and MA50 lower than 5 days before   (TradeManager R4, Professor)
- above_ma50  = close > MA50                                     (short sleeve lane A entries)
Added for later use: MA200, 30-day realised volatility, and a label risk_on / neutral / risk_off
(risk_on = close > MA50 > MA200, risk_off = down). One SIGNAL event only when the label changes.
"""
import math
import time
from datetime import datetime, timezone

import requests

from agent_events import log_event

_cache = {"at": 0.0, "closes": []}  # [(YYYY-MM-DD, close)] closed days, oldest first
_last_label = {"v": ""}


def _closes() -> list[tuple[str, float]]:
    if time.time() - _cache["at"] > 3600 or not _cache["closes"]:
        k = requests.get("https://api.binance.com/api/v3/klines",
                         params={"symbol": "BTCUSDT", "interval": "1d", "limit": 400}, timeout=20).json()
        rows = [(datetime.fromtimestamp(x[0] / 1000, timezone.utc).strftime("%Y-%m-%d"), float(x[4])) for x in k[:-1]]
        _cache.update(at=time.time(), closes=rows)
    return _cache["closes"]


def _state(c: list[float]) -> dict:
    ma50, ma50_prev = sum(c[-50:]) / 50, sum(c[-55:-5]) / 50
    ma200 = sum(c[-200:]) / 200 if len(c) >= 200 else None
    rets = [math.log(b / a) for a, b in zip(c[-31:], c[-30:])]
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets)) * math.sqrt(365)
    down = c[-1] < ma50 and ma50 < ma50_prev
    risk_on = c[-1] > ma50 and (ma200 is None or ma50 > ma200)
    return {"close": c[-1], "ma50": round(ma50, 2), "ma50_prev": round(ma50_prev, 2),
            "ma200": round(ma200, 2) if ma200 else None, "vol30_annual": round(vol, 3),
            "down": down, "above_ma50": c[-1] > ma50,
            "label": "risk_off" if down else "risk_on" if risk_on else "neutral"}


def current() -> dict:
    """Regime on the last closed day."""
    rows = _closes()
    st = _state([v for _, v in rows])
    st["day"] = rows[-1][0]
    if st["label"] != _last_label["v"]:
        _last_label["v"] = st["label"]
        log_event("RegimeAgent", "marche", "SIGNAL", "BTC",
                  f"BTC {st['close']:,.0f} vs MM50 {st['ma50']:,.0f} (MM50 {'en baisse' if st['ma50'] < st['ma50_prev'] else 'en hausse'}), "
                  f"MM200 {st['ma200']:,.0f}, volatilite 30 j {st['vol30_annual']:.0%}", result=st["label"])
    return st


def by_day() -> dict[str, str]:
    """Label per UTC day (each day judged on its own close, no look-ahead), for the Professor."""
    rows = _closes()
    out = {}
    for i in range(55, len(rows)):
        out[rows[i][0]] = _state([v for _, v in rows[:i + 1]])["label"]
    return out
