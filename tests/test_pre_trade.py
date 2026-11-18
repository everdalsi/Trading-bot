"""Offline tests for agency.pre_trade_research (user request 18/11). Run: python -m unittest tests.test_pre_trade"""
import sys
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency  # noqa: E402

DOWN = {"close": 64000, "ma50": 72000, "ma50_prev": 73000, "down": True}
UP = {"close": 90000, "ma50": 80000, "ma50_prev": 79000, "down": False}
MIXED = {"close": 70000, "ma50": 72000, "ma50_prev": 71000, "down": False}


def _regime(state):
    return types.SimpleNamespace(current=lambda: state)


def _trades(n, pnl, symbol="ZECUSDT", age_days=1, strategy="HL_COPY"):
    t = time.time() - age_days * 86400
    return [{"symbol": symbol, "pnl": pnl, "pnl_pct": pnl, "time_out": t, "strategy": strategy} for _ in range(n)]


class TestPreTradeResearch(unittest.TestCase):
    def run_check(self, regime_state, side, trades=(), symbol="SOLUSDT", **ctx):
        with mock.patch.dict(sys.modules, {"regime": _regime(regime_state)}), \
                mock.patch.object(agency, "_closed", return_value=list(trades)), \
                mock.patch.object(agency, "_funding_crowd_veto", return_value=""):
            return agency.pre_trade_research("crypto_copy", symbol, {"side": side, **ctx})

    def test_no_long_in_a_down_regime_but_shorts_go(self):
        self.assertIn("regime baissier", self.run_check(DOWN, "LONG"))
        self.assertEqual(self.run_check(DOWN, "SHORT"), "")

    def test_no_short_in_an_up_regime(self):
        self.assertIn("regime haussier", self.run_check(UP, "SHORT"))
        self.assertEqual(self.run_check(UP, "LONG"), "")

    def test_mixed_regime_allows_both(self):
        self.assertEqual(self.run_check(MIXED, "LONG"), "")
        self.assertEqual(self.run_check(MIXED, "SHORT"), "")

    def test_hedges_and_pairs_skip_the_regime_gate(self):
        self.assertEqual(self.run_check(DOWN, "LONG", hedge=True), "")
        self.assertEqual(self.run_check(DOWN, "LONG", pair=True), "")

    def test_symbol_memory_blocks_a_repeated_loser_then_forgets(self):
        losers = _trades(10, -0.5, symbol="ZECUSDT")
        self.assertIn("historique perdant", self.run_check(MIXED, "SHORT", losers, symbol="ZECUSDT"))
        old = _trades(10, -0.5, symbol="ZECUSDT", age_days=20)
        self.assertEqual(self.run_check(MIXED, "SHORT", old, symbol="ZECUSDT"), "")
        hedges = _trades(10, -0.5, symbol="BTCUSDT", strategy="HL_HEDGE")
        self.assertEqual(self.run_check(MIXED, "SHORT", hedges, symbol="BTCUSDT"), "")

    def test_cooldown_after_exit(self):
        just_closed = [{"symbol": "SOLUSDT", "pnl": 1.0, "pnl_pct": 1.0, "time_out": time.time() - 600}]
        self.assertIn("delai apres sortie", self.run_check(MIXED, "SHORT", just_closed))
        long_ago = [{**just_closed[0], "time_out": time.time() - 2 * 3600}]
        self.assertEqual(self.run_check(MIXED, "SHORT", long_ago), "")

    def test_stoploss_guard_pauses_one_side(self):
        now = time.time()
        stops = [{"symbol": f"C{i}USDT", "pnl": -2.0, "pnl_pct": -8.0, "side": "SHORT", "time_out": now - 900 * i,
                  "exit_reason": "R3 : stop catastrophe : -8.1 %"} for i in range(1, 4)]
        self.assertIn("pause SHORT", self.run_check(MIXED, "SHORT", stops))
        self.assertEqual(self.run_check(MIXED, "LONG", stops), "")          # the other side stays open
        old = [{**s, "time_out": now - 5 * 3600} for s in stops]
        self.assertEqual(self.run_check(MIXED, "SHORT", old), "")

    def test_other_portfolios_untouched(self):
        with mock.patch.dict(sys.modules, {"regime": _regime(DOWN)}):
            self.assertEqual(agency.pre_trade_research("actions_us", "AAPL", {"side": "LONG"}), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
