"""MemeHunter v2: momentum triggers, creator checks and the smart exits (volume fade, creator selling)."""
import os
import sys
import tempfile
import time

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import meme_hunter
import security_agent


def _pair(token: str, price: float = 1.0, vol_m5: float = 12_000, vol_h1: float = 144_000) -> dict:
    return {"pairAddress": f"pair-{token}", "baseToken": {"address": token, "symbol": token.upper()},
            "priceUsd": str(price), "liquidity": {"usd": 500_000}, "volume": {"m5": vol_m5, "h1": vol_h1},
            "txns": {"m5": {"buys": 30, "sells": 10}}, "priceChange": {"m5": 2.0},
            "pairCreatedAt": (time.time() - 48 * 3600) * 1000}


@pytest.fixture
def hunter(tmp_path, monkeypatch):
    monkeypatch.setattr(meme_hunter, "CANDS", tmp_path / "meme_candidates.jsonl")
    monkeypatch.setattr(meme_hunter, "OUT", tmp_path / "meme_hunter.json")
    monkeypatch.setattr(meme_hunter, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(meme_hunter, "_trend_rank", lambda s: None)
    return meme_hunter


def test_vol_accel_compares_last_5_min_with_the_rest_of_the_hour():
    assert meme_hunter.vol_accel({"volume": {"m5": 3_900, "h1": 3_900 + 11 * 1_000}}) == 3.9


def test_vol_accel_unknown_when_the_hour_has_no_other_volume():
    assert meme_hunter.vol_accel({"volume": {"m5": 500, "h1": 500}}) is None


def test_is_breakout_true_above_the_previous_12_candles():
    candles = [[0, 1, 9.0, 1, 1, 1]] + [[0, 1, 1.0 + i / 100, 1, 1, 1] for i in range(12)]
    assert meme_hunter.is_breakout(candles, 1.2) is True  # the current candle's own high (9.0) is ignored


def test_is_breakout_false_below_the_hour_high():
    candles = [[0, 1, 1, 1, 1, 1]] + [[0, 1, 2.0, 1, 1, 1] for _ in range(12)]
    assert meme_hunter.is_breakout(candles, 1.5) is False


def test_is_breakout_unknown_with_too_few_candles():
    assert meme_hunter.is_breakout([[0, 1, 1, 1, 1, 1]] * 3, 5.0) is None


def test_momentum_trigger_names_each_reason():
    assert meme_hunter.momentum_trigger(4.0, False) == "volume x4.0"
    assert meme_hunter.momentum_trigger(1.0, True) == "cassure du plus haut 1 h"
    assert meme_hunter.momentum_trigger(4.0, True) == "cassure du plus haut 1 h + volume x4.0"
    assert meme_hunter.momentum_trigger(1.0, None) == ""
    assert meme_hunter.momentum_trigger(None, None) == ""


def test_fade_count_needs_consecutive_low_volume_checks():
    assert meme_hunter.fade_count(0, 10_000, 3_000) == 1
    assert meme_hunter.fade_count(2, 10_000, 4_000) == 3
    assert meme_hunter.fade_count(2, 10_000, 9_000) == 0
    assert meme_hunter.fade_count(2, 0, 0) == 0  # entry volume unknown (older shadow trades)


def test_creator_dumped_only_on_a_material_drop():
    assert meme_hunter.creator_dumped(0.05, 0.02) is True
    assert meme_hunter.creator_dumped(0.05, 0.04) is False
    assert meme_hunter.creator_dumped(0.001, 0.0) is False  # creator held almost nothing at entry
    assert meme_hunter.creator_dumped(0.05, None) is False
    assert meme_hunter.creator_dumped(None, 0.0) is False


def test_security_agent_reads_the_creator_share():
    report = {"creator": "abc", "creatorBalance": 50, "token": {"supply": 1_000}}
    assert security_agent._creator_pct_rugcheck(report) == 0.05
    assert security_agent._creator_pct_rugcheck({"token": {"supply": 1_000}}) is None
    assert security_agent._creator_pct_goplus({"creator_percent": "0.12"}) == 0.12
    assert security_agent._creator_pct_goplus({"creator_percent": ""}) is None


def test_security_check_uses_the_callers_age_threshold_and_keeps_the_default(monkeypatch):
    young_pair = [{"pairAddress": "p", "baseToken": {"symbol": "YNG"}, "liquidity": {"usd": 500_000},
                   "pairCreatedAt": (time.time() - 3 * 3600) * 1000}]

    class Answer:
        status_code = 200

        @staticmethod
        def json():
            return young_pair

    monkeypatch.setattr(security_agent.requests, "get", lambda *a, **k: Answer())
    monkeypatch.setattr(security_agent, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(security_agent, "_rugcheck", lambda mint: ([], {"creator_pct": 0.01}))

    default_ok, default_reasons, _ = security_agent.check("solana", "tok-age", "court_terme")
    hunter_ok, _, details = security_agent.check("solana", "tok-age", "memes", min_age_h=1.0)

    assert default_ok is False and "paire agee" in default_reasons[0]
    assert hunter_ok is True and details["creator_pct"] == 0.01


def test_scan_trades_on_momentum_keeps_a_control_group_and_vetoes_a_heavy_creator(hunter, monkeypatch):
    pairs = {"hot": _pair("hot", vol_m5=48_000, vol_h1=48_000 + 11 * 12_000),  # x4.0
             "calm": _pair("calm"),                                             # x1.0
             "dev": _pair("dev", vol_m5=48_000, vol_h1=48_000 + 11 * 12_000)}
    creators = {"hot": 0.03, "calm": 0.0, "dev": 0.5}
    monkeypatch.setattr(hunter, "discover", lambda: [("solana", t) for t in pairs])
    monkeypatch.setattr(hunter, "pairs_for", lambda chain, toks: {t: pairs[t] for t in toks})
    monkeypatch.setattr(hunter, "breakout_1h", lambda chain, pair, price: False)
    asked = {}

    def fake_check(chain, tok, portfolio, **thresholds):
        asked[tok] = thresholds
        return True, [], {"creator_pct": creators[tok]}

    monkeypatch.setattr(security_agent, "check", fake_check)

    assert hunter.scan() == 3
    assert asked["hot"] == {"min_age_h": hunter.MIN_AGE_H, "min_liq_usd": hunter.MIN_LIQ}  # the hunter's own thresholds

    rows = {r["token"]: r for r in hunter._load()}
    assert rows["hot"]["decision"] == "trade" and rows["hot"]["trigger"] == "volume x4.0"
    assert rows["hot"]["shadow"]["control"] is False and rows["hot"]["shadow"]["vol_m5_entry"] == 48_000
    assert rows["calm"]["decision"] == "temoin" and rows["calm"]["shadow"]["control"] is True
    assert rows["dev"]["decision"] == "rien" and "createur" in rows["dev"]["why"] and "shadow" not in rows["dev"]


def _open_row(token: str, control: bool, age_s: float, creator_pct=None) -> dict:
    return {"at": time.time() - age_s, "chain": "solana", "token": token, "symbol": token.upper(), "price": 1.0,
            "pair": f"pair-{token}", "follow": {"5m": 1.0}, "decision": "temoin" if control else "trade",
            "trigger": "" if control else "volume x4.0", "breakout_1h": False, "security_ok": True,
            "shadow": {"entry": 1.01, "cost": 0.01, "usd": 10.0, "peak": 1.0, "half_sold": False, "realized": 0.0,
                       "open": True, "control": control, "vol_m5_entry": 48_000, "fade_n": 0,
                       "creator_pct": creator_pct, "creator_at": time.time() - age_s}}


def test_follow_closes_a_momentum_trade_when_volume_fades_but_not_the_control(hunter, monkeypatch):
    hunter._save([_open_row("hot", False, 1_200), _open_row("calm", True, 1_200)])
    faded = {t: _pair(t, price=1.1, vol_m5=5_000) for t in ("hot", "calm")}
    monkeypatch.setattr(hunter, "pairs_for", lambda chain, toks: {t: faded[t] for t in toks})

    for _ in range(hunter.FADE_CONFIRM):
        hunter.follow()

    rows = {r["token"]: r for r in hunter._load()}
    assert rows["hot"]["shadow"]["open"] is False
    assert rows["hot"]["shadow"]["exit_reason"].startswith("volume retombe")
    assert rows["calm"]["shadow"]["open"] is True


def test_follow_does_not_use_the_fade_exit_before_the_minimum_hold(hunter, monkeypatch):
    hunter._save([_open_row("hot", False, 120)])
    faded = {"hot": _pair("hot", price=1.1, vol_m5=5_000)}
    monkeypatch.setattr(hunter, "pairs_for", lambda chain, toks: {t: faded[t] for t in toks})

    for _ in range(hunter.FADE_CONFIRM + 1):
        hunter.follow()

    assert hunter._load()[0]["shadow"]["open"] is True


def test_follow_closes_when_the_creator_sells(hunter, monkeypatch):
    hunter._save([_open_row("hot", False, 1_200, creator_pct=0.05)])
    steady = {"hot": _pair("hot", price=1.1, vol_m5=48_000)}
    monkeypatch.setattr(hunter, "pairs_for", lambda chain, toks: {t: steady[t] for t in toks})
    monkeypatch.setattr(security_agent, "creator_pct", lambda chain, token: 0.01)

    hunter.follow()

    shadow = hunter._load()[0]["shadow"]
    assert shadow["open"] is False and shadow["exit_reason"] == "le createur vend"


def test_summary_separates_momentum_trades_from_the_control_group(hunter):
    closed = _open_row("hot", False, 1_200)
    closed["shadow"].update(open=False, net=2.5, exit_reason="volume retombe de 60 %")
    unread = _open_row("blind", True, 1_200)
    unread["breakout_1h"] = None  # GeckoTerminal did not answer: not a clean control row
    hunter._save([closed, _open_row("calm", True, 1_200), unread])

    result = hunter.summary()

    assert result["breakout_unknown"] == 1 and result["creator_unknown"] == 3
    assert result["by_group"]["elan"] == {"n": 1, "closed": 1, "net": 2.5}
    assert result["by_group"]["temoin"] == {"n": 1, "closed": 0, "net": 0}
    assert result["exits"] == {"volume retombe de 60 %": 1}
    assert result["trade_signals"] == 1
    assert result["paper_allowed"] is False
