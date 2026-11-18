"""L5 Cash-and-carry (market-neutral, measurement first): buy BTC/ETH spot and sell the same amount of a Binance
quarterly future when the future trades far enough above spot; the gap shrinks to zero at expiry.

Per coin, the nearest quarterly with >= 14 days left. Annualised gap = (future / spot - 1) x 365 / days left.
Entry when that gap, minus the annualised round-trip cost, is > LAB_L5_MIN_APR. Exit at expiry (both legs closed
at the spot price, as the future converges) or early when the gap falls under LAB_L5_EXIT_APR.
Note: COIN-M futures are inverse contracts; the lab books them as a plain short of the same dollar notional,
which is close enough for a measurement and errs on no side systematically. Access to these products from
Australia is not verified: this slot only measures.
"""
from __future__ import annotations

import os
from datetime import date

from lab.ledger import SLIPPAGE, SPOT_FEE, LabLedger

ENV = os.environ.get
COINS = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT"}
FUT_FEE = float(ENV("LAB_L5_FUT_FEE", "0.0005"))
MIN_APR = float(ENV("LAB_L5_MIN_APR", "0.08"))
EXIT_APR = float(ENV("LAB_L5_EXIT_APR", "0.02"))
MIN_DAYS = 14


def annualised(fut: float, spot: float, days: int) -> float:
    return (fut / spot - 1) * 365 / days if days > 0 and spot > 0 else 0.0


def round_trip_apr(days: int) -> float:
    return (2 * SPOT_FEE + 2 * FUT_FEE + 4 * SLIPPAGE) * 365 / days if days > 0 else float("inf")


class CashAndCarry:
    name, label = "L5", "Comptant / contrat trimestriel (neutre, mesure)"

    def __init__(self):
        self.marks: dict = {}

    def step(self, ledger: LabLedger, prices: dict, ctx: dict) -> list[str]:
        today = ctx["now"].date()
        futures = ctx["data"].delivery_futures()
        st = ledger.state.setdefault("open", {})
        out, self.marks = [], {}
        for pair, spot_sym in COINS.items():
            spot = prices.get(spot_sym)
            if not spot:
                continue
            self.marks[f"{pair}:SPOT"] = spot
            pos = st.get(pair)
            if pos:
                fut = next((f for f in futures if f["symbol"] == pos["future"]), None)
                days = (date.fromisoformat(pos["expiry"]) - today).days
                if fut:
                    pos["last_fut"] = fut["price"]
                # at expiry the future is worth spot; if the quote vanished just before, settle at spot too
                fut_px = spot if days <= 0 or (not fut and days <= 1) else (fut["price"] if fut else pos.get("last_fut"))
                if fut_px:
                    self.marks[pos["future"]] = fut_px
                why = ("echeance" if days <= 0 or (not fut and days <= 1)
                       else "ecart refermé" if fut and annualised(fut["price"], spot, days) < EXIT_APR else "")
                if why and ledger.close_legs([(pos["fut_key"], fut_px or spot), (pos["spot_key"], spot)], why):
                    del st[pair]
                    out.append(f"CLOSE {pair} ({why})")
                continue
            if ledger.halted:
                continue
            cands = [f for f in futures if f["pair"] == pair
                     and (date.fromisoformat(f["expiry"]) - today).days >= MIN_DAYS]
            if not cands:
                continue
            f = min(cands, key=lambda f: f["expiry"])
            days = (date.fromisoformat(f["expiry"]) - today).days
            apr = annualised(f["price"], spot, days)
            ledger.state.setdefault("last_apr", {})[pair] = round(apr, 4)
            leg = ledger.initial_cash / len(COINS) / 2
            if apr - round_trip_apr(days) > MIN_APR and ledger.cash >= 2 * leg * 0.99:
                fut_key, spot_key = ledger.open_legs([
                    {"symbol": f["symbol"], "side": "SHORT", "price": f["price"], "amount_usd": leg,
                     "fee_rate": FUT_FEE, "meta": {"slot": "L5"}},
                    {"symbol": f"{pair}:SPOT", "side": "LONG", "price": spot, "amount_usd": leg,
                     "fee_rate": SPOT_FEE, "meta": {"slot": "L5"}}])
                st[pair] = {"future": f["symbol"], "expiry": f["expiry"], "fut_key": fut_key, "spot_key": spot_key,
                            "last_fut": f["price"]}
                self.marks[f["symbol"]] = f["price"]
                out.append(f"OPEN {pair} ecart annualise {apr:.1%} jusqu'au {f['expiry']}")
        return out
