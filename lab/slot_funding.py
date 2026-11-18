"""L4 Funding carry (market-neutral): short a Hyperliquid perp + buy the same coin spot, collect the funding.

A SHORT perp is paid while funding > 0. Both legs have the same notional, so the coin's price move cancels out;
what is left is funding received minus fees, slippage and any perp/spot gap. Positive-funding side only (the
mirror trade would need to short spot).
Rules (spec L4, env-tunable): candidates = top LAB_L4_TOP perps by open interest that also trade on Binance spot;
entry when the 8 h funding >= LAB_L4_ENTRY_8H on the last 3 hourly readings AND expected funding over
LAB_L4_HOLD_H hours > 2x the full round-trip cost; exit when the 8 h funding < LAB_L4_EXIT_8H on 2 readings in a
row, or the perp/spot gap > LAB_L4_BASIS_MAX. Open carries stay managed even after leaving the top list.
At most LAB_L4_MAX carries, each LAB_L4_SIZE of the slot's start (half per leg).
"""
from __future__ import annotations

import os

from lab.ledger import PERP_FEE, SLIPPAGE, SPOT_FEE, LabLedger

ENV = os.environ.get
TOP = int(ENV("LAB_L4_TOP", "20"))
ENTRY_8H = float(ENV("LAB_L4_ENTRY_8H", "0.0003"))
EXIT_8H = float(ENV("LAB_L4_EXIT_8H", "0.00005"))
HOLD_H = float(ENV("LAB_L4_HOLD_H", "72"))
MAX_CARRIES = int(ENV("LAB_L4_MAX", "5"))
SIZE = float(ENV("LAB_L4_SIZE", "0.20"))
BASIS_MAX = float(ENV("LAB_L4_BASIS_MAX", "0.01"))
READING_S = 3300   # HL funding changes every hour: one reading per ~hour
CASH_SLACK = 0.99  # ledger.open trims to the cash left after fees; do not refuse a leg for a few cents


def round_trip_cost() -> float:
    """Cost of opening and closing both legs, as a fraction of one leg's notional."""
    return 2 * SPOT_FEE + 2 * PERP_FEE + 4 * SLIPPAGE


class FundingCarry:
    name, label = "L4", "Encaisser le financement (neutre)"

    def __init__(self):
        self.marks: dict = {}

    def step(self, ledger: LabLedger, prices: dict, ctx: dict) -> list[str]:
        data, now_ts = ctx["data"], ctx["now"].timestamp()
        st = ledger.state
        carries, hist = st.setdefault("carries", {}), st.setdefault("hist", {})
        perps = data.hl_perps()
        if not perps:
            return []
        top = {p["coin"] for p in sorted(perps, key=lambda r: r["oi_usd"], reverse=True)[:TOP]}
        wanted = top | set(carries)                       # open carries stay managed outside the top list
        spot = data.spot_prices(f"{c}USDT" for c in wanted)
        by_coin = {p["coin"]: p for p in perps if p["coin"] in wanted and f"{p['coin']}USDT" in spot}

        actions = self._accrue(ledger, carries, by_coin, now_ts)
        new_reading = now_ts - st.get("last_reading", 0) >= READING_S
        if new_reading:
            st["last_reading"] = now_ts
            for coin, p in by_coin.items():
                hist[coin] = (hist.get(coin, []) + [p["funding_h"] * 8])[-3:]
        actions += self._exits(ledger, carries, by_coin, spot, new_reading)
        if new_reading and not ledger.halted:
            candidates = {c: p for c, p in by_coin.items() if c in top}
            actions += self._entries(ledger, carries, hist, candidates, spot, now_ts)
        self.marks = {}
        for coin, c in carries.items():
            if coin in by_coin:
                c["last_perp"], c["last_spot"] = by_coin[coin]["mark"], spot[f"{coin}USDT"]
            if c.get("last_perp") and c.get("last_spot"):
                self.marks[f"{coin}:PERP"], self.marks[f"{coin}:SPOT"] = c["last_perp"], c["last_spot"]
        return actions

    @staticmethod
    def _accrue(ledger, carries, by_coin, now_ts) -> list[str]:
        """Pay or collect funding on every open short perp leg for the time elapsed since the last step. When a
        coin has no fresh data this round, its hours are kept and paid at the next round that has the rate."""
        total = 0.0
        for coin, c in carries.items():
            pos, p = ledger.positions.get(c["perp"]), by_coin.get(coin)
            if not pos or not p:
                continue
            hours = max(0.0, (now_ts - c.get("accrued_to", now_ts)) / 3600)
            c["accrued_to"] = now_ts
            usd = p["funding_h"] * hours * pos["qty"] * p["mark"]  # short leg: paid when funding > 0
            if usd:
                ledger.accrue_funding(usd)
                c["funding"] = c.get("funding", 0.0) + usd
                total += usd
        return [f"FUNDING {total:+.4f}$"] if abs(total) >= 0.01 else []

    @staticmethod
    def _close(ledger, carries, coin, perp_px, spot_px, why) -> str:
        c = carries[coin]
        if ledger.close_legs([(c["perp"], perp_px), (c["spot"], spot_px)], why):
            carries.pop(coin)
            return f"CLOSE carry {coin} ({why}, funding {c.get('funding', 0):+.2f}$)"
        return f"CLOSE carry {coin} incomplet ({why}), nouvel essai au prochain tour"

    def _exits(self, ledger, carries, by_coin, spot, new_reading) -> list[str]:
        out = []
        for coin in list(carries):
            p, sp = by_coin.get(coin), spot.get(f"{coin}USDT")
            if not p or not sp:
                continue
            c = carries[coin]
            if new_reading:
                c["low"] = c.get("low", 0) + 1 if p["funding_h"] * 8 < EXIT_8H else 0
            if c.get("low", 0) >= 2:
                out.append(self._close(ledger, carries, coin, p["mark"], sp, "funding retombe"))
            elif abs(p["mark"] / sp - 1) > BASIS_MAX:
                out.append(self._close(ledger, carries, coin, p["mark"], sp, "ecart perp/spot"))
        return out

    @staticmethod
    def _entries(ledger, carries, hist, candidates, spot, now_ts) -> list[str]:
        out = []
        leg = ledger.initial_cash * SIZE / 2
        for p in sorted(candidates.values(), key=lambda p: p["funding_h"], reverse=True):
            coin = p["coin"]
            if len(carries) >= MAX_CARRIES or ledger.cash < 2 * leg * CASH_SLACK:
                break
            readings = hist.get(coin, [])
            if coin in carries or len(readings) < 3 or min(readings) < ENTRY_8H:
                continue
            if p["funding_h"] * HOLD_H <= 2 * round_trip_cost():
                continue
            sp = spot[f"{coin}USDT"]
            perp_key, spot_key = ledger.open_legs([
                {"symbol": f"{coin}:PERP", "side": "SHORT", "price": p["mark"], "amount_usd": leg,
                 "fee_rate": PERP_FEE, "meta": {"slot": "L4"}},
                {"symbol": f"{coin}:SPOT", "side": "LONG", "price": sp, "amount_usd": leg,
                 "fee_rate": SPOT_FEE, "meta": {"slot": "L4"}}])
            carries[coin] = {"perp": perp_key, "spot": spot_key, "funding": 0.0, "accrued_to": now_ts,
                             "last_perp": p["mark"], "last_spot": sp}
            out.append(f"OPEN carry {coin} ({p['funding_h'] * 8 * 100:.3f}%/8h, 2x{leg:.0f}$)")
        return out
