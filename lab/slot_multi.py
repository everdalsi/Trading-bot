"""L8 Multi-asset trend: long-or-flat trend following on markets other than crypto alone (classic time-series
momentum), sized by volatility, rebalanced weekly at the daily close.

Signal per asset: close > MA200 and MA50 > MA200 -> hold it, otherwise stay out. Weight = LAB_L8_TARGET_VOL /
(number of assets x the asset's annualised 60-day volatility), capped at LAB_L8_CAP; the rest stays in cash.
Rebalanced every LAB_L8_DAYS days. Fills at the last daily close, fee LAB_L8_FEE per side.
"""
from __future__ import annotations

import math
import os
from datetime import date

from lab.ledger import LabLedger

ENV = os.environ.get
ASSETS = {"GC=F": "Or", "CL=F": "Pétrole", "^GSPC": "S&P 500", "^IXIC": "Nasdaq", "EURUSD=X": "EUR/USD",
          "TLT": "Obligations US 20 ans", "BTC-USD": "Bitcoin"}
TARGET_VOL = float(ENV("LAB_L8_TARGET_VOL", "0.10"))
CAP = float(ENV("LAB_L8_CAP", "0.30"))
EVERY_DAYS = int(ENV("LAB_L8_DAYS", "7"))
FEE = float(ENV("LAB_L8_FEE", "0.0005"))


def target_weights(series: dict, n_assets: int | None = None) -> dict:
    """series: {symbol: [close, ...]} oldest first -> {symbol: weight} for the assets in an up-trend.
    Weights are divided by the FULL universe size, so a few missing sources never inflate the others."""
    n = n_assets or len(series) or 1
    out = {}
    for sym, closes in series.items():
        if len(closes) < 200:
            continue
        ma50, ma200 = sum(closes[-50:]) / 50, sum(closes[-200:]) / 200
        if not (closes[-1] > ma200 and ma50 > ma200):
            continue
        rets = [math.log(b / a) for a, b in zip(closes[-61:-1], closes[-60:]) if a > 0 and b > 0]
        if len(rets) < 20:
            continue
        mean = sum(rets) / len(rets)
        vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets) * 252)
        if vol > 0:
            out[sym] = min(CAP, TARGET_VOL / (n * vol))
    return out


class MultiAssetTrend:
    name, label = "L8", "Tendance multi-actifs (or, pétrole, indices, devises, obligations, BTC)"

    def __init__(self):
        self.marks: dict = {}

    def step(self, ledger: LabLedger, prices: dict, ctx: dict) -> list[str]:
        data, today = ctx["data"], ctx["now"].date()
        series = {s: [c for _, c in data.yahoo_daily(s)] for s in ASSETS}
        series = {s: c for s, c in series.items() if c}
        self.marks = {s: c[-1] for s, c in series.items()}
        last = ledger.state.get("last_rebalance")
        held = {p["symbol"] for p in ledger.positions.values()}
        if ledger.halted or len(series) < len(ASSETS) // 2 or not held <= set(series):
            return []  # never rebalance blind: every held asset must have a fresh price
        if last and (today - date.fromisoformat(last)).days < EVERY_DAYS:
            return []
        actions = self._rebalance(ledger, target_weights(series, len(ASSETS)))
        ledger.state["last_rebalance"] = today.isoformat()  # only once the rebalance went through
        return actions

    def _rebalance(self, ledger: LabLedger, weights: dict) -> list[str]:
        equity = ledger.mark(self.marks)
        held: dict = {}
        for key, p in ledger.positions.items():
            held.setdefault(p["symbol"], []).append(key)
        out = []
        for sym, keys in held.items():  # sells first
            px = self.marks.get(sym)
            if not px:
                continue
            value = sum(ledger._value(ledger.positions[k], px) for k in keys)
            excess = value - weights.get(sym, 0.0) * equity
            for k in keys:
                if excess <= 1:
                    break
                lot = ledger._value(ledger.positions[k], px)
                ledger.reduce(k, px, min(excess, lot), "reequilibrage hebdo")
                excess -= lot
            if weights.get(sym, 0.0) == 0:
                out.append(f"SELL {ASSETS.get(sym, sym)} (tendance perdue)")
        for sym, w in weights.items():
            px = self.marks.get(sym)
            value = sum(ledger._value(p, px) for p in ledger.positions.values() if p["symbol"] == sym) if px else 0
            missing = w * equity - value
            if px and missing > 1 and ledger.cash > 1:
                ledger.open(sym, "LONG", px, missing, fee_rate=FEE, meta={"slot": "L8"})
                out.append(f"BUY {ASSETS.get(sym, sym)} {missing:.2f}$ (poids {w:.0%})")
        return out
