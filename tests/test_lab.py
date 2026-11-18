"""Offline tests for the paper trading lab (lab/). Run: python -m unittest tests.test_lab -v"""
import json
import math
import random
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import mirrors, scoreboard  # noqa: E402
from lab.ledger import LabLedger  # noqa: E402
from lab.runner import Lab  # noqa: E402
from lab.slot_basis import CashAndCarry  # noqa: E402
from lab.slot_funding import FundingCarry  # noqa: E402
from lab.slot_grid import GridRange, adx  # noqa: E402
from lab.slot_multi import MultiAssetTrend, target_weights  # noqa: E402
from lab.slot_pairs import PairsTrading, fit_pairs  # noqa: E402
from lab.slots import BuyAndHold, Rebalance, all_slots  # noqa: E402

T0 = datetime(2026, 11, 18, 9, tzinfo=timezone.utc)


class FakeData:
    """Stands in for lab.data: every source is a plain attribute the test sets."""

    def __init__(self):
        self.perps, self.spot, self.futures, self.candles, self.daily = [], {}, [], {}, {}

    def hl_perps(self):
        return self.perps

    def spot_prices(self, symbols):
        return {s: self.spot[s] for s in symbols if s in self.spot}

    def delivery_futures(self):
        return self.futures

    def klines(self, symbol, interval, limit, ttl=0):
        return self.candles.get((symbol, interval), [])

    def yahoo_daily(self, symbol, ttl=0):
        return self.daily.get(symbol, [])


def ctx(data, now=T0):
    return {"data": data, "now": now}


class TestLabLedger(unittest.TestCase):
    def test_round_trip_costs_are_charged(self):
        led = LabLedger("t", initial_cash=1000.0, fee_rate=0.001, slippage=0.0)
        key = led.open("BTCUSDT", "LONG", 100.0, 500.0)
        trade = led.close(key, 100.0)
        self.assertAlmostEqual(trade["pnl_gross"], 0.0, places=6)
        self.assertAlmostEqual(trade["pnl"], -1.0, places=6)
        self.assertAlmostEqual(led.cash, 999.0, places=6)
        self.assertAlmostEqual(led.fees_paid, 1.0, places=6)

    def test_fees_scale_with_leverage(self):
        led = LabLedger("t", initial_cash=1000.0, fee_rate=0.001, slippage=0.0)
        key = led.open("BTCUSDT", "LONG", 100.0, 100.0, leverage=3)
        self.assertAlmostEqual(led.positions[key]["fee_in"], 0.3, places=6)

    def test_slippage_moves_fills_against_us(self):
        led = LabLedger("t", initial_cash=1000.0, fee_rate=0.0, slippage=0.01)
        key = led.open("BTCUSDT", "LONG", 100.0, 100.0)
        self.assertAlmostEqual(led.positions[key]["price_in"], 101.0)
        self.assertLess(led.close(key, 100.0)["pnl"], 0)

    def test_mark_is_a_liquidation_value(self):
        led = LabLedger("t", initial_cash=1000.0, fee_rate=0.001, slippage=0.0)
        led.open("BTCUSDT", "LONG", 100.0, 500.0)
        self.assertLess(led.mark({"BTCUSDT": 100.0}), 999.5 + 1e-9)       # entry fee AND exit fee counted

    def test_partial_reduce_books_a_trade_and_keeps_rest(self):
        led = LabLedger("t", initial_cash=1000.0, fee_rate=0.0, slippage=0.0)
        key = led.open("BTCUSDT", "LONG", 100.0, 400.0)
        trade = led.reduce(key, 110.0, 110.0)
        self.assertAlmostEqual(trade["pnl"], 10.0, places=6)
        self.assertAlmostEqual(led.positions[key]["amount_usd"], 300.0, places=6)
        self.assertAlmostEqual(led.mark({"BTCUSDT": 110.0}), 1040.0, places=4)

    def test_close_after_reload_updates_the_trade(self):
        led = LabLedger("t", initial_cash=1000.0, fee_rate=0.0, slippage=0.0)
        led.open("BTCUSDT", "LONG", 100.0, 100.0)
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "t.json")
            led.save(path)
            back = LabLedger.load(path)
        back.close(next(iter(back.positions)), 110.0)
        self.assertEqual(back.stats()["total"], 1)
        self.assertAlmostEqual(back.trades[0]["pnl"], 10.0, places=6)

    def test_open_legs_never_leaves_a_lone_leg(self):
        led = LabLedger("t", initial_cash=1000.0)
        with self.assertRaises(ValueError):
            led.open_legs([{"symbol": "A", "side": "SHORT", "price": 10.0, "amount_usd": 100.0},
                           {"symbol": "B", "side": "LONG", "price": 0.0, "amount_usd": 100.0}])
        self.assertEqual(led.positions, {})

    def test_circuit_breaker_halts_and_blocks_opens(self):
        led = LabLedger("t", initial_cash=1000.0)
        led.snapshot("2026-11-18", 790.0)
        self.assertTrue(led.halted)
        with self.assertRaises(RuntimeError):
            led.open("BTCUSDT", "LONG", 100.0, 10.0)

    def test_curve_starts_at_initial_cash(self):
        led = LabLedger("t", initial_cash=1000.0)
        led.snapshot("2026-11-18", 990.0)
        self.assertEqual(led.curve(), [["2026-11-18", 1000.0], ["2026-11-18", 990.0]])


class TestPhase1Slots(unittest.TestCase):
    def test_buy_and_hold_buys_once(self):
        led, slot = LabLedger("L10", fee_rate=0.0, slippage=0.0), BuyAndHold()
        self.assertTrue(slot.step(led, {"BTCUSDT": 100.0}))
        self.assertEqual(slot.step(led, {"BTCUSDT": 50.0}), [])
        self.assertAlmostEqual(led.mark({"BTCUSDT": 200.0}), 2000.0, places=4)

    def test_rebalance_initial_weights_then_drift(self):
        led, slot = LabLedger("L9", fee_rate=0.0, slippage=0.0), Rebalance(band=0.05)
        slot.step(led, {"BTCUSDT": 100.0, "ETHUSDT": 10.0})
        self.assertEqual(slot.step(led, {"BTCUSDT": 102.0, "ETHUSDT": 10.0}), [])
        p2 = {"BTCUSDT": 200.0, "ETHUSDT": 10.0}
        self.assertTrue(slot.step(led, p2))
        eq, held = led.mark(p2), slot._holdings(led, p2)
        self.assertAlmostEqual(held["BTCUSDT"] / eq, 0.5, places=3)
        self.assertAlmostEqual(led.cash / eq, 0.2, places=3)


class TestFundingCarry(unittest.TestCase):
    def setUp(self):
        self.data = FakeData()
        self.data.perps = [{"coin": "DOGE", "funding_h": 0.00015, "oi_usd": 5e8, "mark": 0.2},
                           {"coin": "BTC", "funding_h": 0.00001, "oi_usd": 9e9, "mark": 100.0}]
        self.data.spot = {"DOGEUSDT": 0.2, "BTCUSDT": 100.0}

    def test_enters_after_three_readings_collects_then_exits(self):
        led, slot = LabLedger("L4"), FundingCarry()
        for h in range(3):
            acts = slot.step(led, {}, ctx(self.data, T0 + timedelta(hours=h)))
        self.assertTrue(any("OPEN carry DOGE" in a for a in acts))
        self.assertNotIn("BTC", led.state["carries"])                  # funding too low to pay the costs
        cash = led.cash
        slot.step(led, {}, ctx(self.data, T0 + timedelta(hours=4)))
        self.assertGreater(led.cash, cash)                              # funding collected on the short leg
        self.data.perps[0]["funding_h"] = 0.0
        for h in (5, 6):
            acts = slot.step(led, {}, ctx(self.data, T0 + timedelta(hours=h)))
        self.assertTrue(any("CLOSE carry DOGE" in a for a in acts))
        self.assertEqual(led.positions, {})
        self.assertGreater(led.funding_net, 0)

    def test_open_carry_still_managed_after_leaving_the_top_list(self):
        import lab.slot_funding as sf
        led, slot = LabLedger("L4"), FundingCarry()
        for h in range(3):
            slot.step(led, {}, ctx(self.data, T0 + timedelta(hours=h)))
        self.assertIn("DOGE", led.state["carries"])
        old = sf.TOP
        sf.TOP = 1                                                         # DOGE is no longer in the top list
        try:
            self.data.perps[0]["funding_h"] = 0.0
            for h in (3, 4):
                acts = slot.step(led, {}, ctx(self.data, T0 + timedelta(hours=h)))
            self.assertTrue(any("CLOSE carry DOGE" in a for a in acts))
        finally:
            sf.TOP = old


class TestPairs(unittest.TestCase):
    @staticmethod
    def _series():
        rnd = random.Random(7)
        b = [100.0]
        for _ in range(719):
            b.append(b[-1] * math.exp(rnd.gauss(0, 0.01)))
        spread, a = 0.0, []
        for x in b:
            spread = 0.93 * spread + rnd.gauss(0, 0.01)                 # mean-reverting gap, half-life ~10 h
            a.append(math.exp(0.5 + math.log(x) + spread))
        noise = [100 * math.exp(rnd.gauss(0, 0.2)) for _ in b]          # unrelated coin
        return a, b, noise

    def test_finds_the_cointegrated_pair_only(self):
        a, b, noise = self._series()
        models = fit_pairs({"AAA": a, "BBB": b, "ZZZ": noise})
        self.assertEqual([(m["a"], m["b"]) for m in models], [("AAA", "BBB")])

    def test_opens_on_stretch_and_closes_on_return(self):
        a, b, _ = self._series()
        data = FakeData()
        data.candles = {("AAAUSDT", "1h"): [{"t": i, "close": v} for i, v in enumerate(a)],
                        ("BBBUSDT", "1h"): [{"t": i, "close": v} for i, v in enumerate(b)]}
        slot, led = PairsTrading(), LabLedger("L6")
        import lab.slot_pairs as sp
        old = sp.UNIVERSE
        sp.UNIVERSE = ["AAA", "BBB"]
        try:
            slot._refit(led, data, "2026-11-18")
            m = led.state["models"][0]
            pb = b[-1]
            stretched = math.exp(m["alpha"] + m["mean"] + 3 * m["sd"]) * pb ** m["beta"]
            data.spot = {"AAAUSDT": stretched, "BBBUSDT": pb}
            acts = slot.step(led, {}, ctx(data))
            self.assertTrue(any("OPEN AAA/BBB" in x and "SHORT AAA" in x for x in acts))
            data.spot["AAAUSDT"] = math.exp(m["alpha"] + m["mean"]) * pb ** m["beta"]
            acts = slot.step(led, {}, ctx(data))
            self.assertTrue(any("CLOSE AAA/BBB" in x for x in acts))
            self.assertEqual(led.positions, {})
        finally:
            sp.UNIVERSE = old


class TestGrid(unittest.TestCase):
    @staticmethod
    def _flat_candles(n=100):
        rnd = random.Random(3)
        out, p = [], 100.0
        for _ in range(n):
            p = 100 + rnd.uniform(-1, 1)
            out.append({"high": p + 0.5, "low": p - 0.5, "close": p})
        return out

    def test_adx_low_in_a_flat_market(self):
        self.assertLess(adx(self._flat_candles()), 20)

    def test_buys_dips_sells_rebound_and_breaks(self):
        data, slot, led = FakeData(), GridRange(), LabLedger("L7", fee_rate=0.0, slippage=0.0)
        candles = self._flat_candles()
        candles[-1] = {**candles[-1], "close": 100.0}
        data.candles = {("BTCUSDT", "4h"): candles}
        slot.step(led, {"BTCUSDT": 100.0}, ctx(data))                    # switch the grid on, centre 100
        self.assertTrue(led.state["BTCUSDT"]["active"])
        acts = slot.step(led, {"BTCUSDT": 97.5}, ctx(data))             # crosses -1 % and -2 %
        self.assertEqual(sum("BUY" in a for a in acts), 2)
        acts = slot.step(led, {"BTCUSDT": 99.5}, ctx(data))             # -2 % lot sold at -1 %
        self.assertEqual(sum("SELL" in a for a in acts), 1)
        acts = slot.step(led, {"BTCUSDT": 92.0}, ctx(data))             # -8 %: grid broken
        self.assertTrue(any("GRID OFF" in a for a in acts))
        self.assertEqual(led.positions, {})


class TestMultiAsset(unittest.TestCase):
    def test_only_uptrends_get_a_weight(self):
        up = [100 * 1.002 ** i * (1 + 0.01 * math.sin(i)) for i in range(250)]
        down = [100 * 0.998 ** i * (1 + 0.01 * math.sin(i)) for i in range(250)]
        w = target_weights({"UP": up, "DOWN": down})
        self.assertIn("UP", w)
        self.assertNotIn("DOWN", w)
        self.assertLessEqual(w["UP"], 0.30)

    def test_weekly_rebalance(self):
        data = FakeData()
        up = [100 * 1.002 ** i * (1 + 0.01 * math.sin(i)) for i in range(250)]
        data.daily = {"GC=F": [(f"d{i}", v) for i, v in enumerate(up)], "^GSPC": [(f"d{i}", v) for i, v in enumerate(up)],
                      "TLT": [(f"d{i}", v) for i, v in enumerate(reversed(up))], "BTC-USD": [(f"d{i}", v) for i, v in enumerate(up)]}
        slot, led = MultiAssetTrend(), LabLedger("L8")
        acts = slot.step(led, {}, ctx(data))
        self.assertTrue(acts and all("BUY" in a for a in acts))
        self.assertEqual(slot.step(led, {}, ctx(data, T0 + timedelta(days=2))), [])   # not before 7 days
        self.assertNotIn("TLT", {p["symbol"] for p in led.positions.values()})


class TestCashAndCarry(unittest.TestCase):
    def test_opens_on_wide_gap_and_closes_at_expiry(self):
        data, slot, led = FakeData(), CashAndCarry(), LabLedger("L5")
        expiry = (T0 + timedelta(days=60)).date().isoformat()
        data.futures = [{"symbol": "BTCUSD_X", "pair": "BTCUSD", "price": 103.0, "expiry": expiry}]
        acts = slot.step(led, {"BTCUSDT": 100.0, "ETHUSDT": 10.0}, ctx(data))      # ~18 %/year gap
        self.assertTrue(any("OPEN BTCUSD" in a for a in acts))
        acts = slot.step(led, {"BTCUSDT": 120.0, "ETHUSDT": 10.0}, ctx(data, T0 + timedelta(days=61)))
        self.assertTrue(any("CLOSE BTCUSD (echeance)" in a for a in acts))
        self.assertEqual(led.positions, {})
        self.assertGreater(led.cash, 1000.0)                            # the 3 % gap, minus costs, was earned

    def test_both_coins_open_despite_fees(self):
        data, slot, led = FakeData(), CashAndCarry(), LabLedger("L5")
        expiry = (T0 + timedelta(days=60)).date().isoformat()
        data.futures = [{"symbol": "BTCUSD_X", "pair": "BTCUSD", "price": 103.0, "expiry": expiry},
                        {"symbol": "ETHUSD_X", "pair": "ETHUSD", "price": 10.3, "expiry": expiry}]
        acts = slot.step(led, {"BTCUSDT": 100.0, "ETHUSDT": 10.0}, ctx(data))
        self.assertEqual(sum("OPEN" in a for a in acts), 2)


class TestMirrorsAndScoreboard(unittest.TestCase):
    def test_ledger_summary(self):
        d = {"cash": 400, "positions": {"a": {"amount_usd": 500}}, "trades": [{"pnl": 10}, {"pnl": -5}, {"x": 1}]}
        s = mirrors.summarize_ledger(d)
        self.assertEqual((s["equity"], s["n_trades"], s["pnl_closed"], s["win_rate"], s["fees"]),
                         (900.0, 2, 5.0, 50.0, None))

    def test_results_per_agent_in_a_shared_portfolio(self):
        d = {"cash": 0, "positions": {}, "trades": [{"pnl": 5, "strategy": "copy"}, {"pnl": -2, "strategy": "copy"},
                                                       {"pnl": 9, "strategy": "consensus"}, {"pnl": 1, "lane": "A"}]}
        agents = mirrors.summarize_ledger(d)["agents"]
        self.assertEqual(list(agents), ["consensus", "copy", "A"])        # best first
        self.assertEqual(agents["copy"], {"trades": 2, "pnl": 3.0, "win_rate": 50.0})

    def test_real_cached_ledgers_if_present(self):
        cache = Path(__file__).resolve().parent.parent / "dashboard" / "cache"
        for fname in ("hl_copy_portfolio.json", "meme_sim.json"):
            if (cache / fname).exists():
                s = mirrors.summarize_ledger(json.loads((cache / fname).read_text(encoding="utf-8")))
                self.assertGreater(s["equity"], 0, fname)

    def test_candidate_needs_every_check(self):
        snaps = [[f"2026-01-{i + 1:02d}", 1000 + i * 2] for i in range(31)]
        bench = [[d, 1000 + i] for i, (d, _) in enumerate(snaps)]
        self.assertEqual(scoreboard.build_row("L6", "p", snaps, bench, trades=150, win_rate=55, fees=3)["status"],
                         "candidat")
        self.assertEqual(scoreboard.build_row("L6", "p", snaps, bench, trades=20, win_rate=55, fees=3)["status"],
                         "mesure")
        self.assertEqual(scoreboard.build_row("L1", "p", snaps, bench, trades=150, win_rate=55, fees=3,
                                              measure_only=True)["status"], "mesure")

    def test_calendar_days_count_gaps(self):
        self.assertEqual(scoreboard.calendar_days([["2026-11-01", 1], ["2026-11-30", 2]]), 30)

    def test_max_drawdown(self):
        self.assertEqual(scoreboard.max_drawdown_pct([["a", 100], ["b", 120], ["c", 90], ["d", 130]]), -25.0)


class TestRunner(unittest.TestCase):
    def test_all_slots_step_and_one_report_per_day(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            (data_dir / "meme_sim.json").write_text(json.dumps({"cash": 1000, "positions": {}, "trades": []}))
            sent, clock, fake = [], [T0], FakeData()

            class Boom:
                name, label, marks = "LX", "plante", {}

                def step(self, *a):
                    raise ValueError("boom")

            slots = all_slots() + [Boom()]
            lab = Lab(data_dir, fetch=lambda s: {"BTCUSDT": 100.0, "ETHUSDT": 10.0},
                      send=lambda t: sent.append(t) or True, now=lambda: clock[0], data=fake, slots=slots)
            board = lab.step()
            lab.step()
            clock[0] += timedelta(days=1)
            lab.step()
            self.assertEqual(len(sent), 2)
            self.assertEqual({r["slot"] for r in board["rows"]},
                             {"L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L10", "LX"})
            self.assertTrue(all(r["status"] == "mesure" for r in board["rows"] if r["slot"] in ("L1", "L2", "L3")))
            reborn = Lab(data_dir, fetch=lambda s: None, send=lambda t: True, data=fake, slots=all_slots())
            self.assertEqual(len(reborn.ledgers["L10"].positions), 1)
            self.assertIsNone(reborn.step())

    def test_lab_has_no_private_api_or_keys(self):
        src = "".join(p.read_text(encoding="utf-8")
                      for p in (Path(__file__).resolve().parent.parent / "lab").glob("*.py"))
        for needle in ("api_key", "API_SECRET", "signature", "/api/v3/order", "privateKey", "exchange(", "/fapi/v1/order"):
            self.assertNotIn(needle, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
