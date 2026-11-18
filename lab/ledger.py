"""LabLedger: StrategyLedger plus realistic costs, partial sells, a circuit breaker and daily equity snapshots.

Costs are always charged (pessimistic by design, spec section 2):
- fee_rate on the NOTIONAL (amount x leverage) of every fill (entry, exit, partial sell);
- slippage moves every fill against us (buy higher, sell lower);
- marking values open positions at what a sale would really bring (exit fee and slippage deducted).
A trade's "pnl" is NET of both fees, "pnl_gross" keeps the price-only result.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strategy_ledger import StrategyLedger, _now_str  # noqa: E402

SPOT_FEE = float(os.environ.get("LAB_SPOT_FEE", "0.001"))        # Binance spot taker 0.10 %
PERP_FEE = float(os.environ.get("LAB_PERP_FEE", "0.00045"))      # Hyperliquid perp taker 0.045 %
SLIPPAGE = float(os.environ.get("LAB_SLIPPAGE", "0.0002"))       # 0.02 % per fill
HALT_DRAWDOWN = float(os.environ.get("LAB_HALT_DRAWDOWN", "0.20"))  # slot stops at -20 % of its start


def _fill(price: float, side: str, opening: bool, slippage: float) -> float:
    """Price actually obtained: worse than quoted on every fill."""
    buying = (side == "LONG") == opening
    return price * (1 + slippage) if buying else price * (1 - slippage)


@dataclass
class LabLedger(StrategyLedger):
    fee_rate: float = SPOT_FEE
    slippage: float = SLIPPAGE
    fees_paid: float = field(default=0.0, init=False)
    funding_net: float = field(default=0.0, init=False)
    halted: str = field(default="", init=False)
    started: str = field(default_factory=_now_str, init=False)
    start_day: str = field(default="", init=False)              # day of the first snapshot (equity = initial_cash)
    snapshots: list = field(default_factory=list, init=False)  # [[YYYY-MM-DD, equity], ...] one per day
    state: dict = field(default_factory=dict, init=False)      # the slot's own memory (models, histories)

    # ---------------------------------------------------------------- trading
    def open(self, symbol: str, side: str, price: float, amount_usd: float,
             leverage: float = 1.0, meta: Optional[dict] = None, fee_rate: Optional[float] = None) -> str:
        """`fee_rate` overrides the ledger's rate for this leg (e.g. a perp leg in a spot ledger)."""
        if self.halted:
            raise RuntimeError(f"{self.name} halted: {self.halted}")
        rate = self.fee_rate if fee_rate is None else fee_rate
        amount_usd = min(amount_usd, self.cash / (1 + rate * leverage))
        fee = amount_usd * leverage * rate
        key = super().open(symbol, side, _fill(price, side, True, self.slippage), amount_usd, leverage, meta)
        self.cash -= fee
        self.fees_paid += fee
        self.positions[key].update({"fee_in": fee, "fee_rate": rate})
        return key

    def close(self, pos_key: str, price: float, reason: str = "") -> Optional[dict]:
        pos = self.positions.get(pos_key)
        if not pos:
            return None
        trade = super().close(pos_key, _fill(price, pos["side"], False, self.slippage), reason)
        fee_out = trade["qty"] * trade["price_out"] * trade.get("leverage", 1.0) * pos.get("fee_rate", self.fee_rate)
        self.cash -= fee_out
        self.fees_paid += fee_out
        trade["pnl_gross"] = trade["pnl"]
        trade["fee_out"] = fee_out
        trade["pnl"] = round(trade["pnl_gross"] - trade.get("fee_in", 0.0) - fee_out, 6)
        return trade

    def reduce(self, pos_key: str, price: float, usd_out: float, reason: str = "") -> Optional[dict]:
        """Sell part of a position worth `usd_out` at `price`; books the sold part as its own closed trade."""
        pos = self.positions.get(pos_key)
        if not pos or usd_out <= 0:
            return None
        value = self._value(pos, price)
        if usd_out >= value * 0.999:
            return self.close(pos_key, price, reason)
        frac = usd_out / value
        part = {**pos, "qty": pos["qty"] * frac, "amount_usd": pos["amount_usd"] * frac,
                "fee_in": pos.get("fee_in", 0.0) * frac}
        pos["qty"] -= part["qty"]
        pos["amount_usd"] -= part["amount_usd"]
        pos["fee_in"] = pos.get("fee_in", 0.0) - part["fee_in"]
        tmp_key = f"{pos_key}_part{len(self.trades) + 1}"
        part["id"] = len(self.trades) + 1
        self.trades.append(part)
        self.positions[tmp_key] = part
        return self.close(tmp_key, price, reason)

    def open_legs(self, legs: list[dict]) -> list[str]:
        """Open several legs as one trade (e.g. short perp + long spot). If any leg fails, the legs already opened
        are closed again at their entry price, so no lone leg is ever left behind; the error is re-raised."""
        keys = []
        try:
            for leg in legs:
                keys.append(self.open(**leg))
        except Exception:
            for key, leg in zip(keys, legs):
                self.close(key, leg["price"], "jambe annulee (2e jambe impossible)")
            raise
        return keys

    def close_legs(self, closes: list[tuple[str, float]], reason: str) -> bool:
        """Close every (key, price) leg; each one is tried even if another fails. True when none is left open."""
        for key, price in closes:
            try:
                self.close(key, price, reason)
            except Exception:
                continue
        return all(key not in self.positions for key, _ in closes)

    def accrue_funding(self, usd: float) -> None:
        """Funding received (> 0) or paid (< 0) by a perp leg."""
        self.cash += usd
        self.funding_net += usd

    # ---------------------------------------------------------------- marking
    @staticmethod
    def _value(pos: dict, price: float) -> float:
        entry, amt = pos["price_in"], pos["amount_usd"]
        move = (price - entry) / entry * pos.get("leverage", 1.0)
        return amt * (1 + move) if pos["side"] == "LONG" else amt * (1 - move)

    def _exit_cost(self, pos: dict, price: float) -> float:
        notional = pos["qty"] * price * pos.get("leverage", 1.0)
        return notional * (pos.get("fee_rate", self.fee_rate) + self.slippage)

    def mark(self, prices: dict) -> float:
        """Liquidation value: each priced position is worth what selling it now would bring (fee and slippage
        deducted); a position without a price keeps its cost."""
        total = self.cash
        for p in self.positions.values():
            px = prices.get(p["symbol"])
            total += self._value(p, px) - self._exit_cost(p, px) if px else p["amount_usd"]
        return round(total, 4)

    def snapshot(self, day: str, equity: float) -> None:
        """Keep one equity point per day (the last one of the day) and apply the circuit breaker."""
        if not self.start_day:
            self.start_day = day
        if self.snapshots and self.snapshots[-1][0] == day:
            self.snapshots[-1][1] = equity
        else:
            self.snapshots.append([day, equity])
        if not self.halted and equity <= self.initial_cash * (1 - HALT_DRAWDOWN):
            self.halted = f"drawdown {HALT_DRAWDOWN:.0%} reached on {day}"

    def curve(self) -> list:
        """Equity curve starting from the untouched initial cash, so day-1 costs and moves count."""
        return ([[self.start_day, self.initial_cash]] if self.start_day else []) + [list(s) for s in self.snapshots]

    # ---------------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        return {**super().to_dict(), "fee_rate": self.fee_rate, "slippage": self.slippage,
                "fees_paid": self.fees_paid, "funding_net": self.funding_net, "halted": self.halted,
                "started": self.started, "start_day": self.start_day, "snapshots": self.snapshots,
                "state": self.state}

    @classmethod
    def from_dict(cls, data: dict) -> "LabLedger":
        obj = cls(name=data["name"], initial_cash=data.get("initial_cash", 1000.0),
                  fee_rate=data.get("fee_rate", SPOT_FEE), slippage=data.get("slippage", SLIPPAGE))
        obj.cash = data.get("cash", obj.initial_cash)
        obj.positions = data.get("positions", {})
        obj.trades = data.get("trades", [])
        for key in ("fees_paid", "funding_net", "halted", "started", "start_day", "snapshots", "state"):
            if key in data:
                setattr(obj, key, data[key])
        # JSON breaks the in-memory link between an open position and its trade entry: restore it, otherwise a
        # position closed after a restart would update only its own copy and the trade would stay "open" forever.
        by_id = {t.get("id"): i for i, t in enumerate(obj.trades)}
        for pos in obj.positions.values():
            i = by_id.get(pos.get("id"))
            if i is not None:
                obj.trades[i] = pos
        return obj
