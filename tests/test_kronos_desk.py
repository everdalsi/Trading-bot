"""Offline tests for kronos_agent scoring, market_desk decisions / shadow book, api_keeper hex rule (06/11)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api_keeper  # noqa: E402
import kronos_agent as ka  # noqa: E402
import market_desk as md  # noqa: E402

H = ka.TF_MS
B = (int(__import__('time').time() * 1000) // H - 40) * H   # recent: older rows are pruned


def test_score_track_and_stats():
    series = {f"S{i}USDT": [(B + t * H, 0, 0, 0, 100 + i * (t - 10), 0, 0) for t in range(30)] for i in range(-5, 6)}
    # forecasts made at candle 10 predicting the true sign for every market
    track = [{"sym": s, "t0": B + 10 * H, "c0": 100.0, "pred": {"1": float(i), "4": float(i), "12": float(i)}}
             for i, s in zip(range(-5, 6), series)]
    track = ka.score_track(track, series)
    st = ka.stats(track)
    assert st["4"]["n"] == 11 and st["4"]["hit"] >= 0.9 and st["4"]["ic"] > 0.9
    assert not ka.trusted(st)                       # far too few forecasts to be trusted


def test_spearman():
    assert abs(ka.spearman([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) - 1) < 1e-9
    assert ka.spearman([1, 1, 1, 1, 1], [1, 2, 3, 4, 5]) == 0.0


def test_universe_filter():
    assert md._eligible("SOLUSDT") and not md._eligible("USDCUSDT") and not md._eligible("BTCUPUSDT")
    assert not md._eligible("WBTCUSDT") and not md._eligible("ETHBTC")


def test_decision_and_kronos_gate():
    chart = {"1h": {"score": 0.8, "trend": "up", "price": 10.0, "atr_pct": 1.0},
             "4h": {"score": 0.8, "trend": "up"}}
    k = {"pred_ret_pct": {"4": -3.0}, "agree": 1.0}
    r = md.score_market("XUSDT", chart, k, False, None)
    assert r["decision"] == "LONG"                  # untrusted Kronos is shown, not used
    r2 = md.score_market("XUSDT", chart, k, True, None)
    assert r2["score"] < r["score"]                 # trusted Kronos pulls the score down
    down = {"1h": {"score": 0.9, "trend": "up", "price": 1.0}, "4h": {"score": 0.6, "trend": "down"}}
    assert md.score_market("YUSDT", down, None, False, None)["decision"] == "WAIT"   # never against the 4 h trend


def test_shadow_book():
    row = {"symbol": "XUSDT", "price": 100.0, "decision": "LONG", "confidence": 0.8, "score": 0.6, "atr_1h_pct": 1.0}
    book = md.update_book({"open": {}, "closed": []}, [row], 0)
    assert book["open"]["XUSDT"]["target"] == 103.0
    book = md.update_book(book, [{**row, "price": 103.5, "decision": "WAIT"}], 60)
    assert not book["open"] and book["closed"][0]["why"] == "objectif"
    assert abs(book["closed"][0]["net_pct"] - (3.5 - md.COST_PCT)) < 1e-6
    assert not md.book_stats(book)["proven"]
    assert md.candidates() == []                    # nothing handed to the FillEngine before proof


def test_hex_rule(tmp_path):
    f = tmp_path / "x.jsonl"
    f.write_text('{"pool": "0x' + "a" * 64 + '"}\n{"private_key": "0x' + "b" * 64 + '"}\n')
    found = api_keeper.secrets_scan([f])
    assert found and not any(x["masked"].startswith("0xaa") for x in found)   # pool id ignored, key caught
