"""
strategy_ledger.py -- Standalone, isolated per-strategy paper ledger + metrics.

STATUS: NOT wired into bot.py or the live trading loop. Zero imports from
bot.py/agents/* on purpose, so importing this module can never trigger any
bot.py module-level side effect (bot.py starts ws_manager + prefetches caches
at import time). This is a design-exploration brick only -- see the
parallel-backtesting design notes in the accompanying report for how it
would eventually be wired into a *separate* local script that runs N MICRO
scoring variants against the same live price feed, each writing to its own
StrategyLedger instance, before anything is considered for bot.py itself.

Conventions (winrate/profit_factor/max_drawdown/sharpe formulas) are kept
deliberately consistent with the two calculation sites already live in this
repo, so results are comparable side-by-side with production stats:
  - bot.py:get_stats() / bot.py:backtest_strategy()  (win_rate, profit_factor,
    max_drawdown via equity-curve rolling max)
  - agents/performance_tracker.py:PerformanceTracker.get_global_stats()
    (sharpe, sortino omitted here to keep this brick dependency-free; only
    the metrics that don't need numpy are reproduced -- add numpy-based ones
    later by literally calling PerformanceTracker.get_global_stats({"trades":
    ledger.trades}) since its input shape is just {"trades": [...]}, which a
    StrategyLedger's .trades list already matches field-for-field).

Trade dict shape mirrors bot.py's sim["trades"] entries (symbol, side,
price_in, price_out, qty, amount_usd, pnl, pnl_pct, time_in, time_out,
exit_reason) so a ledger's .trades can be dropped straight into
PerformanceTracker.get_global_stats({"trades": ...}) with no translation.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class StrategyLedger:
    """Isolated paper-trading ledger for exactly one strategy variant.

    Every StrategyLedger owns its own cash/positions/trades -- nothing here
    ever touches bot.py's shared `sim` dict, so N variants can run side by
    side (in a separate process/script) without one variant's drawdown
    distorting another's numbers or the live bot's real ones.
    """
    name: str
    initial_cash: float = 1000.0
    cash: float = field(init=False)
    positions: dict = field(default_factory=dict, init=False)
    trades: list = field(default_factory=list, init=False)

    def __post_init__(self):
        self.cash = self.initial_cash

    # ------------------------------------------------------------------ #
    # Trading
    # ------------------------------------------------------------------ #
    def open(self, symbol: str, side: str, price: float, amount_usd: float,
              leverage: float = 1.0, meta: Optional[dict] = None) -> str:
        """Open a paper position. side is 'LONG' or 'SHORT'."""
        if amount_usd <= 0 or price <= 0:
            raise ValueError("amount_usd and price must be > 0")
        if amount_usd > self.cash:
            amount_usd = self.cash  # clamp instead of silently going negative
        qty = amount_usd / price
        self.cash -= amount_usd
        trade_id = len(self.trades) + 1
        pos_key = f"{self.name}_{symbol}_{side}_{trade_id}"
        trade = {
            "id": trade_id, "symbol": symbol, "side": side,
            "price_in": price, "price_out": None,
            "qty": qty, "amount_usd": amount_usd, "leverage": leverage,
            "time_in": _now_str(), "time_out": None,
            "pnl": None, "pnl_pct": None, "exit_reason": None,
            "meta": meta or {},
        }
        self.trades.append(trade)
        self.positions[pos_key] = trade
        return pos_key

    def close(self, pos_key: str, price: float, reason: str = "") -> Optional[dict]:
        pos = self.positions.pop(pos_key, None)
        if not pos:
            return None
        entry, amt, lev = pos["price_in"], pos["amount_usd"], pos.get("leverage", 1.0)
        if pos["side"] == "LONG":
            pnl = (price - entry) / entry * amt * lev
            pnl_pct = (price - entry) / entry * 100 * lev
        else:
            pnl = (entry - price) / entry * amt * lev
            pnl_pct = (entry - price) / entry * 100 * lev
        self.cash += amt + pnl
        pos.update({
            "price_out": price, "time_out": _now_str(),
            "pnl": round(pnl, 6), "pnl_pct": round(pnl_pct, 3),
            "exit_reason": reason,
        })
        return pos

    def equity(self, mark_prices: Optional[dict] = None) -> float:
        """Total equity = cash + open positions, marked at entry cost unless
        mark_prices ({symbol: price}) is given for a mark-to-market value."""
        total = self.cash
        for pos in self.positions.values():
            if mark_prices and pos["symbol"] in mark_prices:
                price = mark_prices[pos["symbol"]]
                entry, amt, lev = pos["price_in"], pos["amount_usd"], pos.get("leverage", 1.0)
                if pos["side"] == "LONG":
                    unreal = (price - entry) / entry * amt * lev
                else:
                    unreal = (entry - price) / entry * amt * lev
                total += amt + unreal
            else:
                total += pos["amount_usd"]
        return round(total, 4)

    # ------------------------------------------------------------------ #
    # Metrics -- same formulas as bot.py:get_stats()/backtest_strategy()
    # ------------------------------------------------------------------ #
    def stats(self) -> dict:
        closed = [t for t in self.trades if t.get("pnl") is not None]
        if not closed:
            return {
                "name": self.name, "total": 0, "wins": 0, "losses": 0,
                "win_rate": 0.0, "profit_factor": 0.0, "total_pnl": 0.0,
                "max_drawdown": 0.0, "best": 0.0, "worst": 0.0,
                "cash": round(self.cash, 4), "open_positions": len(self.positions),
            }
        wins = [t for t in closed if t["pnl"] > 0]
        losses = [t for t in closed if t["pnl"] <= 0]
        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))

        # Max drawdown on the ledger's own realized-pnl equity curve, same
        # rolling-max-vs-current approach as bot.py:backtest_strategy().
        equity = self.initial_cash
        peak = equity
        max_dd = 0.0
        for t in closed:
            equity += t["pnl"]
            peak = max(peak, equity)
            if peak > 0:
                dd = (equity - peak) / peak
                max_dd = min(max_dd, dd)

        return {
            "name": self.name,
            "total": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 1),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0.0,
            "total_pnl": round(sum(t["pnl"] for t in closed), 4),
            "max_drawdown": round(max_dd * 100, 2),
            "best": round(max(t["pnl"] for t in closed), 4),
            "worst": round(min(t["pnl"] for t in closed), 4),
            "cash": round(self.cash, 4),
            "open_positions": len(self.positions),
        }

    # ------------------------------------------------------------------ #
    # Persistence -- own file, never sim_portfolio_v7.json
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "name": self.name, "initial_cash": self.initial_cash,
            "cash": self.cash, "positions": self.positions, "trades": self.trades,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StrategyLedger":
        obj = cls(name=data["name"], initial_cash=data.get("initial_cash", 1000.0))
        obj.cash = data.get("cash", obj.initial_cash)
        obj.positions = data.get("positions", {})
        obj.trades = data.get("trades", [])
        return obj

    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        import os
        os.replace(tmp, path)  # atomic, same pattern as bot.py:save_data()

    @classmethod
    def load(cls, path: str) -> "StrategyLedger":
        with open(path) as f:
            return cls.from_dict(json.load(f))


class StrategyRegistry:
    """Holds N named StrategyLedger variants side by side for comparison."""

    def __init__(self):
        self._ledgers: dict[str, StrategyLedger] = {}

    def register(self, name: str, initial_cash: float = 1000.0) -> StrategyLedger:
        if name not in self._ledgers:
            self._ledgers[name] = StrategyLedger(name=name, initial_cash=initial_cash)
        return self._ledgers[name]

    def get(self, name: str) -> StrategyLedger:
        return self._ledgers[name]

    def compare(self) -> dict:
        return {name: ledger.stats() for name, ledger in self._ledgers.items()}


# ---------------------------------------------------------------------- #
# Unit tests (stdlib only -- run with: python strategy_ledger.py)
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    import unittest

    class TestStrategyLedger(unittest.TestCase):
        def test_open_close_long_win(self):
            l = StrategyLedger("A", initial_cash=1000.0)
            key = l.open("BTCUSDT", "LONG", price=100.0, amount_usd=100.0)
            self.assertEqual(l.cash, 900.0)
            trade = l.close(key, price=110.0, reason="TP")
            self.assertAlmostEqual(trade["pnl"], 10.0, places=4)
            self.assertAlmostEqual(l.cash, 1010.0, places=4)
            self.assertEqual(len(l.positions), 0)

        def test_open_close_short_loss(self):
            l = StrategyLedger("B", initial_cash=1000.0)
            key = l.open("ETHUSDT", "SHORT", price=100.0, amount_usd=100.0)
            trade = l.close(key, price=110.0, reason="SL")
            self.assertAlmostEqual(trade["pnl"], -10.0, places=4)
            self.assertAlmostEqual(l.cash, 990.0, places=4)

        def test_amount_clamped_to_cash(self):
            l = StrategyLedger("C", initial_cash=50.0)
            key = l.open("SOLUSDT", "LONG", price=10.0, amount_usd=100.0)
            self.assertEqual(l.cash, 0.0)
            self.assertEqual(l.positions[key]["amount_usd"], 50.0)

        def test_stats_win_rate_and_profit_factor(self):
            l = StrategyLedger("D", initial_cash=1000.0)
            k1 = l.open("BTCUSDT", "LONG", 100, 100); l.close(k1, 120, "TP")   # +20
            k2 = l.open("BTCUSDT", "LONG", 100, 100); l.close(k2, 90, "SL")    # -10
            k3 = l.open("BTCUSDT", "LONG", 100, 100); l.close(k3, 130, "TP")   # +30
            s = l.stats()
            self.assertEqual(s["total"], 3)
            self.assertEqual(s["wins"], 2)
            self.assertAlmostEqual(s["win_rate"], 66.7, places=1)
            self.assertAlmostEqual(s["profit_factor"], 5.0, places=2)  # 50/10

        def test_max_drawdown_nonpositive(self):
            l = StrategyLedger("E", initial_cash=1000.0)
            k1 = l.open("BTCUSDT", "LONG", 100, 500); l.close(k1, 80, "SL")   # -100
            s = l.stats()
            self.assertLessEqual(s["max_drawdown"], 0.0)

        def test_registry_compare(self):
            reg = StrategyRegistry()
            a = reg.register("aggressive", 1000.0)
            b = reg.register("conservative", 1000.0)
            k = a.open("BTCUSDT", "LONG", 100, 200); a.close(k, 105, "TP")
            cmp = reg.compare()
            self.assertIn("aggressive", cmp)
            self.assertIn("conservative", cmp)
            self.assertEqual(cmp["conservative"]["total"], 0)
            self.assertEqual(cmp["aggressive"]["total"], 1)

        def test_roundtrip_save_load(self):
            import tempfile, os
            l = StrategyLedger("F", initial_cash=1000.0)
            k = l.open("BTCUSDT", "LONG", 100, 200); l.close(k, 110, "TP")
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "ledger_F.json")
                l.save(path)
                reloaded = StrategyLedger.load(path)
                self.assertEqual(reloaded.stats(), l.stats())

    unittest.main(verbosity=2)
