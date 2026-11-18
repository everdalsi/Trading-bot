"""L7 Grid in sideways markets: buy small dips and sell the rebound, only when the market has no trend.

Per asset (BTC, ETH; half of the slot each): the regime is checked on 4 h candles (refreshed hourly). A grid is
switched on only when ADX(14) < LAB_L7_ADX and the close is inside the Bollinger bands (20, 2). It is centred on
the price at that moment, with 5 buy levels 1 %, 2 % ... 5 % below; each level buys 1/5 of the asset's budget
when the price crosses it downwards, and that lot is sold one level higher. The grid is switched off (all lots
sold) when the price leaves the band by more than LAB_L7_BREAK (7 % from the centre by default).
"""
from __future__ import annotations

import os

from lab.ledger import LabLedger

ENV = os.environ.get
ASSETS = ("BTCUSDT", "ETHUSDT")
STEP = float(ENV("LAB_L7_STEP", "0.01"))
LEVELS = int(ENV("LAB_L7_LEVELS", "5"))
ADX_MAX = float(ENV("LAB_L7_ADX", "20"))
BREAK = float(ENV("LAB_L7_BREAK", "0.07"))


def adx(candles: list[dict], n: int = 14) -> float | None:
    if len(candles) < 2 * n + 1:
        return None
    trs, pdm, ndm = [], [], []
    for prev, cur in zip(candles[:-1], candles[1:]):
        up, down = cur["high"] - prev["high"], prev["low"] - cur["low"]
        pdm.append(up if up > down and up > 0 else 0.0)
        ndm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(cur["high"] - cur["low"], abs(cur["high"] - prev["close"]), abs(cur["low"] - prev["close"])))

    def smooth(v):
        out = [sum(v[:n])]
        for x in v[n:]:
            out.append(out[-1] - out[-1] / n + x)
        return out

    tr_s, p_s, n_s = smooth(trs), smooth(pdm), smooth(ndm)
    dx = []
    for t, p, m in zip(tr_s, p_s, n_s):
        if t == 0:
            continue
        pdi, ndi = 100 * p / t, 100 * m / t
        dx.append(100 * abs(pdi - ndi) / (pdi + ndi) if pdi + ndi else 0.0)
    if len(dx) < n:
        return None
    a = sum(dx[:n]) / n
    for x in dx[n:]:
        a = (a * (n - 1) + x) / n
    return a


def in_bollinger(closes: list[float], n: int = 20, k: float = 2.0) -> bool:
    if len(closes) < n:
        return False
    w = closes[-n:]
    mean = sum(w) / n
    sd = (sum((c - mean) ** 2 for c in w) / n) ** 0.5
    return mean - k * sd < closes[-1] < mean + k * sd


class GridRange:
    name, label = "L7", "Grille en marché sans tendance"

    def __init__(self):
        self.marks: dict = {}

    def _regime_ok(self, data, symbol: str) -> bool:
        candles = data.klines(symbol, "4h", 100)
        a = adx(candles)
        return a is not None and a < ADX_MAX and in_bollinger([c["close"] for c in candles])

    def step(self, ledger: LabLedger, prices: dict, ctx: dict) -> list[str]:
        out = []
        self.marks = {}
        for sym in ASSETS:
            px = prices.get(sym)
            if not px:
                continue
            g = ledger.state.setdefault(sym, {"active": False, "lots": {}, "last_px": px})
            last, g["last_px"] = g["last_px"], px
            if g["active"]:
                out += self._run(ledger, sym, g, last, px)
            elif not ledger.halted and self._regime_ok(ctx["data"], sym):
                g.update({"active": True, "center": px, "lots": {}})
                out.append(f"GRID ON {sym} centre {px:.2f}")
        return out

    def _run(self, ledger, sym, g, last, px) -> list[str]:
        center, lots, out = g["center"], g["lots"], []
        if abs(px / center - 1) > BREAK:
            for key in list(lots.values()):
                ledger.close(key, px, "grille cassee")
            g.update({"active": False, "lots": {}})
            return [f"GRID OFF {sym} (sortie de fourchette)"]
        for k in sorted(lots, key=int):  # sell each lot one level above where it was bought
            target = center * (1 - STEP * (int(k) - 1))
            if px >= target:
                ledger.close(lots.pop(k), px, f"niveau -{int(k) - 1}%")
                out.append(f"SELL {sym} lot -{k}%")
        lot_usd = ledger.initial_cash / len(ASSETS) / LEVELS
        for k in range(1, LEVELS + 1):
            level = center * (1 - STEP * k)
            if str(k) not in lots and last > level >= px and ledger.cash >= lot_usd and not ledger.halted:
                lots[str(k)] = ledger.open(sym, "LONG", px, lot_usd, meta={"slot": "L7", "level": k})
                out.append(f"BUY {sym} lot -{k}%")
        return out
