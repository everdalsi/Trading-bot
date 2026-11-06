"""FundingCarry (T1) + Radar (T2): two measurement agents from Hyperliquid's free public info API (06/11 research:
nunchi-trade/agent-cli "Funding Hedge" + "Radar/Pulse", Chainstack HL funding guide). Stdlib only, fail-open.

FundingCarry  carry(symbol, side) -> {hl_8h_pct, bin_8h_pct, daily_pct, we_get_paid, venue_best}
              A SHORT is paid when funding > 0, a LONG is paid when funding < 0. Funding is normalised to 8 h on
              every venue (HL pays hourly, Binance/Bybit every 4-8 h). entry_ok(symbol, side, max_cost_daily_pct)
              -> (bool, why): measurement first (logged), meant to block entries that PAY > max cost per day.
Radar         scan() -> top coins where capital is flowing in NOW: open-interest growth since the last snapshots,
              24 h volume vs open interest, funding pressure, price move. Snapshots in DATA_DIR/radar_hist.json,
              results in DATA_DIR/radar.json (for court terme momentum and MemeHunter, measurement mode).

    start(every_s=300)  thread: refresh both every 5 min -> DATA_DIR/funding_carry.json + radar.json
"""
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
HL = "https://api.hyperliquid.xyz/info"
_cache: dict[str, tuple[float, object]] = {}


def _post(body: dict, ttl: float = 60):
    key = json.dumps(body, sort_keys=True)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    req = urllib.request.Request(HL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    _cache[key] = (time.time(), data)
    return data


def _coin(symbol: str) -> str:
    return symbol.upper().removesuffix("USDT").removesuffix("USDC").removeprefix("1000")


# ------------------------------------------------------------------ FundingCarry
def fundings() -> dict[str, dict[str, float]]:
    """{coin: {venue: funding rate normalised to 8 h (fraction)}} for HL, Binance, Bybit."""
    out: dict[str, dict[str, float]] = {}
    for coin, venues in _post({"type": "predictedFundings"}):
        for venue, info in venues:
            if info and info.get("fundingRate") is not None:
                hours = float(info.get("fundingIntervalHours") or 8)
                out.setdefault(coin, {})[venue] = float(info["fundingRate"]) * 8 / hours
    return out


def carry(symbol: str, side: str) -> dict | None:
    try:
        f = fundings().get(_coin(symbol))
        if not f:
            return None
        hl = f.get("HlPerp")
        ref = hl if hl is not None else next(iter(f.values()))
        sign = 1 if side.upper() == "SHORT" else -1          # shorts receive positive funding
        daily = sign * ref * 3 * 100                          # % of position per day (3 x 8 h)
        best = max(f.items(), key=lambda kv: sign * kv[1])[0]
        return {"hl_8h_pct": round(100 * hl, 4) if hl is not None else None,
                "bin_8h_pct": round(100 * f["BinPerp"], 4) if "BinPerp" in f else None,
                "daily_pct": round(daily, 4), "we_get_paid": daily > 0, "venue_best": best}
    except Exception:
        return None


def entry_ok(symbol: str, side: str, max_cost_daily_pct: float = 0.15) -> tuple[bool, str]:
    c = carry(symbol, side)
    if c is None:
        return True, "funding inconnu"
    if c["daily_pct"] < -max_cost_daily_pct:
        return False, f"{side} paierait {-c['daily_pct']:.3f} %/jour de funding"
    return True, f"carry {c['daily_pct']:+.3f} %/jour"


def carry_report(positions: list[dict]) -> dict:
    """positions: [{symbol, side, usd}] -> carry per position and total USD per day (+ = we earn)."""
    rows, total = [], 0.0
    for p in positions:
        c = carry(p["symbol"], p["side"])
        if c:
            usd_day = p.get("usd", 0) * c["daily_pct"] / 100
            total += usd_day
            rows.append({**p, **c, "usd_day": round(usd_day, 4)})
    return {"positions": rows, "usd_per_day": round(total, 4)}


# ------------------------------------------------------------------ Radar
def _ctxs() -> dict[str, dict]:
    meta, ctxs = _post({"type": "metaAndAssetCtxs"}, ttl=60)
    out = {}
    for u, c in zip(meta["universe"], ctxs):
        try:
            px = float(c["markPx"])
            out[u["name"]] = {"oi_usd": float(c["openInterest"]) * px, "vol_usd": float(c["dayNtlVlm"]),
                              "funding_1h": float(c["funding"]), "px": px,
                              "chg_24h": px / float(c["prevDayPx"]) - 1 if float(c["prevDayPx"]) else 0.0}
        except (TypeError, ValueError, KeyError):
            continue
    return out


def scan(min_oi_usd: float = 2e6, top: int = 15) -> dict:
    now = time.time()
    cur = _ctxs()
    hist_path = DATA_DIR / "radar_hist.json"
    try:
        hist = json.loads(hist_path.read_text(encoding="utf-8"))
    except Exception:
        hist = []
    hist = [h for h in hist if now - h["at"] < 6 * 3600] + [{"at": now, "oi": {k: v["oi_usd"] for k, v in cur.items()}}]
    try:
        hist_path.write_text(json.dumps(hist), encoding="utf-8")
    except Exception:
        pass
    old = min(hist, key=lambda h: abs(now - 3600 - h["at"]))      # snapshot closest to 1 h ago
    span_h = max(0.08, (now - old["at"]) / 3600)
    rows = []
    for coin, c in cur.items():
        if c["oi_usd"] < min_oi_usd:
            continue
        oi0 = old["oi"].get(coin)
        oi_chg = (c["oi_usd"] / oi0 - 1) / span_h if oi0 else 0.0   # per hour
        turnover = c["vol_usd"] / c["oi_usd"] if c["oi_usd"] else 0.0
        score = 40 * min(1.0, max(-1.0, oi_chg / 0.05)) + 25 * min(1.0, turnover / 10) \
            + 20 * min(1.0, abs(c["chg_24h"]) / 0.15) + 15 * min(1.0, abs(c["funding_1h"]) / 0.0005)
        rows.append({"coin": coin, "score": round(score, 1), "oi_chg_1h_pct": round(100 * oi_chg, 2),
                     "turnover": round(turnover, 2), "chg_24h_pct": round(100 * c["chg_24h"], 2),
                     "funding_8h_pct": round(100 * c["funding_1h"] * 8, 4),
                     "direction": "long" if c["chg_24h"] > 0 and oi_chg > 0 else "short" if c["chg_24h"] < 0 and oi_chg > 0 else "flat"})
    rows.sort(key=lambda r: -r["score"])
    return {"at": now, "span_h": round(span_h, 2), "coins": rows[:top], "mode": "mesure (aucun poids)"}


# ------------------------------------------------------------------ service
def refresh(positions: list[dict] | None = None) -> dict:
    out = {}
    try:
        out["radar"] = scan()
        (DATA_DIR / "radar.json").write_text(json.dumps(out["radar"], ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        out["radar_error"] = type(e).__name__
    if positions is not None:
        try:
            out["carry"] = carry_report(positions)
            (DATA_DIR / "funding_carry.json").write_text(json.dumps({"at": time.time(), **out["carry"]}), encoding="utf-8")
        except Exception as e:
            out["carry_error"] = type(e).__name__
    return out


_thread = None

def start(positions_fn=None, every_s: int = 300) -> threading.Thread:
    """Idempotent: bot.py starts agents from auto_start() AND from the Telegram /start command."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return _thread
    """positions_fn() -> [{symbol, side, usd}] of all open positions (the bot passes its ledgers reader)."""
    def loop():
        while True:
            try:
                refresh(positions_fn() if positions_fn else None)
            except Exception:
                pass
            time.sleep(every_s)
    t = threading.Thread(target=loop, name="FundingRadar", daemon=True)
    t.start()
    _thread = t
    return t


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for s, side in (("BTCUSDT", "LONG"), ("SOLUSDT", "SHORT"), ("XRPUSDT", "SHORT"), ("HYPEUSDT", "SHORT")):
        print(s, side, carry(s, side), entry_ok(s, side))
    r = scan()
    for row in r["coins"][:8]:
        print(row)
