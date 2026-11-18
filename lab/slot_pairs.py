"""L6 Pairs trading (market-neutral): two coins that move together; trade the gap when it stretches too far.

Once a day, on 30 days of 1 h candles of LAB_L6_UNIVERSE: Engle-Granger cointegration of every pair (OLS of
log A on log B, then an ADF t-stat on the residual vs the 5 % critical value -3.34), half-life of the gap between
6 and 72 h; the best non-overlapping pairs (at most LAB_L6_MAX) are kept with their parameters.
Every step: z = (log A - beta log B - alpha - mean) / std. |z| > 2 -> sell the expensive leg, buy the cheap one
(perp legs, beta-weighted notional). Exit when |z| < 0.5; stop when |z| > 4 or the trade is older than 3 half-lives.
Each pair uses at most 1/LAB_L6_MAX of the slot's start.
"""
from __future__ import annotations

import math
import os

from lab.ledger import PERP_FEE, LabLedger

ENV = os.environ.get
UNIVERSE = ENV("LAB_L6_UNIVERSE", "BTC,ETH,SOL,BNB,XRP,ADA,DOGE,AVAX,LINK,DOT,LTC,BCH,TRX,ATOM,NEAR").split(",")
MAX_PAIRS = int(ENV("LAB_L6_MAX", "4"))
ADF_CRIT = -3.34
HL_MIN, HL_MAX = 6.0, 72.0
Z_IN, Z_OUT, Z_STOP = 2.0, 0.5, 4.0


def _ols(y: list, x: list) -> tuple[float, float]:
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx == 0:
        return 0.0, 0.0
    beta = sum((a - mx) * (b - my) for a, b in zip(x, y)) / sxx
    return my - beta * mx, beta


def adf_t(resid: list) -> float:
    """t-stat of gamma in  d(s_t) = c + gamma * s_(t-1) + e  (no extra lags)."""
    lag, diff = resid[:-1], [b - a for a, b in zip(resid[:-1], resid[1:])]
    c, gamma = _ols(diff, lag)
    n = len(lag)
    if n < 10:
        return 0.0
    res = [d - c - gamma * s for d, s in zip(diff, lag)]
    s2 = sum(r * r for r in res) / (n - 2)
    mx = sum(lag) / n
    sxx = sum((s - mx) ** 2 for s in lag)
    return gamma / math.sqrt(s2 / sxx) if sxx and s2 > 0 else 0.0


def half_life(resid: list) -> float:
    lag, diff = resid[:-1], [b - a for a, b in zip(resid[:-1], resid[1:])]
    _, phi = _ols(diff, lag)
    return -math.log(2) / phi if phi < 0 else float("inf")


def fit_pairs(closes: dict) -> list[dict]:
    """closes: {coin: [close, ...]} same length. Returns the chosen pair models, best first."""
    coins = [c for c in closes if len(closes[c]) >= 100]
    logs = {c: [math.log(v) for v in closes[c]] for c in coins}
    found = []
    for i, a in enumerate(coins):
        for b in coins[i + 1:]:
            alpha, beta = _ols(logs[a], logs[b])
            if beta <= 0:
                continue
            resid = [ya - alpha - beta * xb for ya, xb in zip(logs[a], logs[b])]
            t, hl = adf_t(resid), half_life(resid)
            if t < ADF_CRIT and HL_MIN <= hl <= HL_MAX:
                mean = sum(resid) / len(resid)
                sd = math.sqrt(sum((r - mean) ** 2 for r in resid) / len(resid))
                if sd > 0:
                    found.append({"a": a, "b": b, "alpha": alpha, "beta": beta, "mean": mean, "sd": sd,
                                  "hl": hl, "t": t})
    chosen, used = [], set()
    for m in sorted(found, key=lambda m: m["t"]):
        if m["a"] not in used and m["b"] not in used:
            chosen.append(m)
            used |= {m["a"], m["b"]}
        if len(chosen) >= MAX_PAIRS:
            break
    return chosen


def zscore(m: dict, pa: float, pb: float) -> float:
    return (math.log(pa) - m["beta"] * math.log(pb) - m["alpha"] - m["mean"]) / m["sd"]


class PairsTrading:
    name, label = "L6", "Paires corrélées (neutre)"

    def __init__(self):
        self.marks: dict = {}

    def _refit(self, ledger: LabLedger, data, day: str, now_ts: float = 0.0) -> None:
        st = ledger.state
        if st.get("model_day") == day or ("model_try" in st and now_ts - st["model_try"] < 3600):
            return
        st["model_try"] = now_ts
        candles = {c: {k["t"]: k["close"] for k in data.klines(f"{c}USDT", "1h", 720) if "t" in k}
                   for c in UNIVERSE}
        candles = {c: s for c, s in candles.items() if len(s) >= 100}
        if len(candles) < 2:
            return
        # align on the candle time: a coin listed later or lagging must not shift the others
        common = sorted(set.intersection(*(set(s) for s in candles.values())))
        keep = {c: s for c, s in candles.items() if len(common) >= 100}
        st["models"] = fit_pairs({c: [s[t] for t in common] for c, s in keep.items()})
        st["model_day"] = day

    def step(self, ledger: LabLedger, prices: dict, ctx: dict) -> list[str]:
        data, now = ctx["data"], ctx["now"]
        self._refit(ledger, data, now.strftime("%Y-%m-%d"), now.timestamp())
        st = ledger.state
        trades = st.setdefault("open", {})
        coins = {m[k] for m in st.get("models", []) for k in ("a", "b")} | {t[k] for t in trades.values()
                                                                            for k in ("a", "b")}
        spot = data.spot_prices(f"{c}USDT" for c in coins)
        self.marks = {f"{c}:PERP": spot[f"{c}USDT"] for c in coins if f"{c}USDT" in spot}
        actions = self._manage(ledger, trades, spot, now.timestamp())
        if not ledger.halted:
            actions += self._enter(ledger, trades, st.get("models", []), spot, now.timestamp())
        return actions

    @staticmethod
    def _manage(ledger, trades, spot, now_ts) -> list[str]:
        out = []
        for key in list(trades):
            t = trades[key]
            pa, pb = spot.get(f"{t['a']}USDT"), spot.get(f"{t['b']}USDT")
            if not pa or not pb:
                continue
            z = zscore(t["model"], pa, pb)
            age_h = (now_ts - t["opened"]) / 3600
            why = ("ecart refermé" if abs(z) < Z_OUT else "stop ecart" if abs(z) > Z_STOP
                   else "trop long" if age_h > 3 * t["model"]["hl"] else "")
            if why and ledger.close_legs([(t["leg_a"], pa), (t["leg_b"], pb)], why):
                del trades[key]
                out.append(f"CLOSE {key} z={z:+.2f} ({why})")
        return out

    @staticmethod
    def _enter(ledger, trades, models, spot, now_ts) -> list[str]:
        out, budget = [], ledger.initial_cash / MAX_PAIRS
        for m in models:
            key = f"{m['a']}/{m['b']}"
            pa, pb = spot.get(f"{m['a']}USDT"), spot.get(f"{m['b']}USDT")
            if key in trades or not pa or not pb or len(trades) >= MAX_PAIRS or ledger.cash < budget * 0.99:
                continue
            z = zscore(m, pa, pb)
            if abs(z) <= Z_IN or abs(z) >= Z_STOP:
                continue
            side_a, side_b = ("SHORT", "LONG") if z > 0 else ("LONG", "SHORT")
            usd_a = budget / (1 + m["beta"])
            leg_a, leg_b = ledger.open_legs([
                {"symbol": f"{m['a']}:PERP", "side": side_a, "price": pa, "amount_usd": usd_a,
                 "fee_rate": PERP_FEE, "meta": {"slot": "L6"}},
                {"symbol": f"{m['b']}:PERP", "side": side_b, "price": pb, "amount_usd": budget - usd_a,
                 "fee_rate": PERP_FEE, "meta": {"slot": "L6"}}])
            trades[key] = {"a": m["a"], "b": m["b"], "leg_a": leg_a, "leg_b": leg_b, "model": m, "opened": now_ts}
            out.append(f"OPEN {key} z={z:+.2f} ({side_a} {m['a']} / {side_b} {m['b']})")
        return out
