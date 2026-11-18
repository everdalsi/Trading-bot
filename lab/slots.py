"""Phase-1 trading slots: L9 passive rebalancing and L10 buy-and-hold benchmark.

A slot is a small object with a `name`, a `label` and `step(ledger, prices)`. `prices` maps Binance spot symbols
(BTCUSDT, ETHUSDT) to floats. Slots never fetch data themselves, so tests can drive them with fixed prices.
"""
from __future__ import annotations

import os

from lab.ledger import LabLedger

CASH = "USDC"


class BuyAndHold:
    """L10: buys 100 % BTC on the first priced step and never sells. Mandatory benchmark."""
    name, label = "L10", "Témoin : acheter et garder BTC"
    symbol = "BTCUSDT"

    def step(self, ledger: LabLedger, prices: dict, ctx: dict | None = None) -> list[str]:
        if ledger.positions or ledger.trades or self.symbol not in prices:
            return []
        ledger.open(self.symbol, "LONG", prices[self.symbol], ledger.cash, meta={"slot": self.name})
        return [f"BUY {self.symbol} (benchmark, once)"]


class Rebalance:
    """L9: fixed basket, rebalanced only when one weight drifts more than `band` points from its target."""
    name, label = "L9", "Rééquilibrage 50 % BTC / 30 % ETH / 20 % USDC"

    def __init__(self, targets: dict | None = None, band: float | None = None):
        self.targets = targets or {"BTCUSDT": 0.50, "ETHUSDT": 0.30, CASH: 0.20}
        self.band = band if band is not None else float(os.environ.get("LAB_REBALANCE_BAND", "0.05"))

    def _holdings(self, ledger: LabLedger, prices: dict) -> dict:
        held = {s: 0.0 for s in self.targets if s != CASH}
        for p in ledger.positions.values():
            held[p["symbol"]] = held.get(p["symbol"], 0.0) + ledger._value(p, prices[p["symbol"]])
        return held

    def step(self, ledger: LabLedger, prices: dict, ctx: dict | None = None) -> list[str]:
        coins = [s for s in self.targets if s != CASH]
        if ledger.halted or any(s not in prices for s in coins):
            return []
        held = self._holdings(ledger, prices)
        equity = ledger.cash + sum(held.values())
        weights = {s: held[s] / equity for s in coins}
        weights[CASH] = ledger.cash / equity
        first = not ledger.positions and not ledger.trades
        if not first and all(abs(weights[s] - self.targets[s]) <= self.band for s in self.targets):
            return []
        actions = []
        for s in coins:  # sells first, so the cash exists for the buys
            excess = held[s] - self.targets[s] * equity
            if excess > 1:
                self._sell(ledger, s, prices[s], excess)
                actions.append(f"SELL {s} {excess:.2f}$")
        for s in coins:
            missing = self.targets[s] * equity - held[s]
            if missing > 1 and ledger.cash > 1:
                ledger.open(s, "LONG", prices[s], missing, meta={"slot": self.name})
                actions.append(f"BUY {s} {missing:.2f}$")
        return actions

    @staticmethod
    def _sell(ledger: LabLedger, symbol: str, price: float, usd: float) -> None:
        for key in [k for k, p in ledger.positions.items() if p["symbol"] == symbol]:  # oldest lot first
            if usd <= 0:
                break
            lot = ledger._value(ledger.positions[key], price)
            ledger.reduce(key, price, min(usd, lot), reason="rebalance")
            usd -= lot


def all_slots() -> list:
    """L4-L10 in display order (L1-L3 are read-only mirrors, see mirrors.py)."""
    from lab.slot_basis import CashAndCarry
    from lab.slot_funding import FundingCarry
    from lab.slot_grid import GridRange
    from lab.slot_multi import MultiAssetTrend
    from lab.slot_pairs import PairsTrading
    return [FundingCarry(), CashAndCarry(), PairsTrading(), GridRange(), MultiAssetTrend(), Rebalance(), BuyAndHold()]
