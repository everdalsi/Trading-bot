"""TopTraders (spec B of ~/.claude/plans/trading-moteur-gestion-et-infos.md): the best Hyperliquid
wallets per category and their consensus per coin. Public data, no key, read-only.

Every 2 h (own thread):
1. leaderboard (stats-data.hyperliquid.xyz, ~34 MB): account >= $50k, month AND week PnL > 0,
   the 200 best by month PnL;
2. for each, the last 30 days of fills (userFillsByTime): keep wallets with >= 20 closing trades
   and a max drawdown of their closed-PnL curve <= 60 % of the account value;
   score = month ROI capped at 100 % x (1 - drawdown) x min(1, trades / 50)   (regular > lucky);
3. open positions (clearinghouseState) of the kept wallets; category by where most of their
   notional sits: majors (BTC, ETH, SOL), memes, alts; top 50 per category;
4. consensus per coin over all kept wallets = sum(side x score) / sum(score), in [-1, +1], only when
   >= 5 wallets hold the coin (else unknown).
Output DATA_DIR/top_traders.json {at, categories: {majors|alts|memes: [...]}, consensus: {BTC: 0.4},
holders: {BTC: n}}. Read by TradeManager R6/R8 (consensus(symbol) handles "SOLUSDT" or "SOL").
SIGNAL event when the consensus of a coin we hold moves by >= 0.3.
Requests are paced (userFills weighs 20 on HL's 1200-per-minute budget).
"""
import json
import os
import threading
import time
from pathlib import Path

import requests

from agent_events import log_event

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "top_traders.json"
LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
INFO = "https://api.hyperliquid.xyz/info"
MIN_ACCOUNT, MIN_TRADES, MAX_DD, MIN_HOLDERS = 50_000, 20, 0.60, 5
# user 06/11: "les 100 meilleurs wallets en fonction des categories de trade qu'on veut prendre" -> 100 per category
# (majors / alts / memes AND universe A / universe B), so the leaderboard depth grows to 500 candidates. userFills
# weighs 20 on HL's 1200/min budget (pause 1.1 s): the full selection runs every SELECT_EVERY_S, positions every hour.
CANDIDATES = int(os.environ.get("TOP_TRADERS_CANDIDATES", 500))
TOP_PER_CAT = int(os.environ.get("TOP_TRADERS_PER_CAT", 100))
SELECT_EVERY_S = int(os.environ.get("TOP_TRADERS_SELECT_S", 6 * 3600))
KEPT = DATA_DIR / "top_traders_kept.json"
MAX_CLOSES_30D, TOP3_MAX_SHARE = 3000, 0.50
MAJORS = {"BTC", "ETH", "SOL"}
MEMES = {"kPEPE", "PEPE", "WIF", "kBONK", "BONK", "POPCAT", "kSHIB", "SHIB", "DOGE", "kFLOKI", "FLOKI", "MEW",
         "BRETT", "TURBO", "MOODENG", "GOAT", "PNUT", "FARTCOIN", "TRUMP", "PENGU", "SPX", "kNEIRO", "NEIRO"}


def _cat(coin: str) -> str:
    return "majors" if coin in MAJORS else "memes" if coin in MEMES else "alts"


def _post(body: dict, pause: float):
    time.sleep(pause)
    r = requests.post(INFO, json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def candidates() -> list[dict]:
    rows = requests.get(LEADERBOARD, timeout=60).json().get("leaderboardRows", [])
    out = []
    for row in rows:
        try:
            acct = float(row.get("accountValue") or 0)
            perf = dict(row.get("windowPerformances", []))
            m_pnl, w_pnl = float(perf["month"]["pnl"]), float(perf["week"]["pnl"])
            if acct >= MIN_ACCOUNT and m_pnl > 0 and w_pnl > 0 and row.get("ethAddress"):
                out.append({"address": row["ethAddress"], "account": acct, "month_pnl": m_pnl,
                            "month_roi": float(perf["month"]["roi"]), "week_roi": float(perf["week"]["roi"])})
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda c: -c["month_pnl"])
    return out[:CANDIDATES]


def regularity(w: dict) -> dict | None:
    """Trades and drawdown over 30 days from the wallet's fills; None if it fails the filters."""
    fills = _post({"type": "userFillsByTime", "user": w["address"],
                   "startTime": int((time.time() - 30 * 86400) * 1000)}, pause=1.1)
    closes = [float(f.get("closedPnl") or 0) for f in sorted(fills, key=lambda f: f.get("time", 0))
              if float(f.get("closedPnl") or 0) != 0]
    if len(closes) < MIN_TRADES:
        return None
    peak = cum = dd = 0.0
    for c in closes:
        cum += c
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    dd_pct = dd / max(w["account"], 1.0)
    if dd_pct > MAX_DD:
        return None
    # V3 (trading-agents-v3.md): luck and market-making filters on the same 30-day fills
    total = sum(closes)
    if len(closes) > MAX_CLOSES_30D:  # market-maker / HFT pattern, not copyable
        return None
    if total <= 0 or sum(sorted(closes, reverse=True)[:3]) > TOP3_MAX_SHARE * total:
        return None  # profit made by 3 trades = luck, not skill
    if len(closes) >= 30 and _bootstrap_p5(closes) <= 0:
        return None  # mean trade not robustly > 0
    score = min(w["month_roi"], 1.0) * (1 - dd_pct) * min(1.0, len(closes) / 50)
    return {**w, "trades_30d": len(closes), "max_dd": round(dd_pct, 3), "score": round(score, 4)}


def _bootstrap_p5(closes: list[float], n: int = 500) -> float:
    """5th percentile of the mean closed PnL over n bootstrap resamples (seeded: reproducible)."""
    import random
    rnd = random.Random(42)
    k = len(closes)
    means = sorted(sum(rnd.choice(closes) for _ in range(k)) / k for _ in range(n))
    return means[int(0.05 * n)]


def positions(addr: str) -> dict[str, tuple[str, float]]:
    st = _post({"type": "clearinghouseState", "user": addr}, pause=0.15)
    out = {}
    for ap in st.get("assetPositions", []):
        p = ap.get("position", {})
        szi = float(p.get("szi") or 0)
        if szi:
            out[p["coin"]] = ("LONG" if szi > 0 else "SHORT", abs(float(p.get("positionValue") or 0)))
    return out


def _key(coin: str) -> str:
    return coin[1:] if coin.startswith("k") and coin[1:].isupper() else coin


def _universes() -> dict[str, set]:
    """Coins of universe A (crypto copy) and B (court terme), from market_desk (single source)."""
    out = {"A": set(), "B": set()}
    try:
        import market_desk
        for u in ("A", "B"):
            out[u] = {s.removesuffix("USDT").removeprefix("1000") for s in market_desk.universe(u)}
    except Exception:
        pass
    return out


def _consensus(wallets: list[dict]) -> tuple[dict, dict]:
    num, den, holders = {}, {}, {}
    for w in wallets:
        for coin, (side, _) in w["positions"].items():
            k = _key(coin)
            num[k] = num.get(k, 0) + (1 if side == "LONG" else -1) * w["score"]
            den[k] = den.get(k, 0) + w["score"]
            holders[k] = holders.get(k, 0) + 1
    return ({c: round(num[c] / den[c], 3) for c in num if holders[c] >= MIN_HOLDERS and den[c] > 0}, holders)


def select() -> list[dict]:
    """Full selection (every SELECT_EVERY_S): leaderboard -> 30-day fills filters -> scored wallets."""
    kept = []
    for w in candidates():
        try:
            r = regularity(w)
        except Exception:
            continue
        if r:
            kept.append(r)
    tmp = KEPT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"at": time.time(), "wallets": kept}), encoding="utf-8")
    tmp.replace(KEPT)
    return kept


def run(full: bool | None = None) -> dict:
    try:
        saved = json.loads(KEPT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if full or full is None and (not saved.get("wallets") or time.time() - saved.get("at", 0) > SELECT_EVERY_S):
        kept = select()
    else:
        kept = [dict(w) for w in saved["wallets"]]
    univ = _universes()
    for r in kept:
        try:
            r["positions"] = positions(r["address"])
        except Exception:
            r["positions"] = {}
        notional = {}
        for coin, (_, val) in r["positions"].items():
            notional[_cat(coin)] = notional.get(_cat(coin), 0) + val
        r["category"] = max(notional, key=notional.get) if notional else "alts"
        nu = {"A": 0.0, "B": 0.0}  # universe category: where most of the wallet's open notional is
        for coin, (_, val) in r["positions"].items():
            for u in ("A", "B"):
                if _key(coin) in univ[u]:
                    nu[u] += val
        r["universe"] = max(nu, key=nu.get) if max(nu.values()) > 0 else None
    cats = {c: sorted([w for w in kept if w["category"] == c], key=lambda w: -w["score"])[:TOP_PER_CAT]
            for c in ("majors", "alts", "memes")}
    ucats = {u: sorted([w for w in kept if w.get("universe") == u], key=lambda w: -w["score"])[:TOP_PER_CAT]
             for u in ("A", "B")}
    consensus, holders = _consensus(kept)
    consensus_a, holders_a = _consensus(ucats["A"])
    consensus_b, holders_b = _consensus(ucats["B"])
    try:
        old = json.loads(OUT.read_text(encoding="utf-8")).get("consensus", {})
    except (OSError, ValueError):
        old = {}
    try:
        import chart_analyst
        held = {s.replace("USDT", "") for s in chart_analyst.held_symbols()}
    except Exception:
        held = set()
    for c in held & set(consensus):
        if c not in old or abs(consensus[c] - old[c]) >= 0.3:
            log_event("TopTraders", "crypto_copy", "SIGNAL", c, f"consensus top wallets {old.get(c, 'inconnu')} -> "
                      f"{consensus[c]:+.2f} ({holders[c]} wallets)", score=consensus[c],
                      result="haussier" if consensus[c] > 0.2 else "baissier" if consensus[c] < -0.2 else "partage")
    data = {"at": time.time(), "kept": len(kept),
            "categories": {c: [{k: w[k] for k in ("address", "score", "month_roi", "trades_30d", "max_dd", "account")}
                               for w in ws] for c, ws in cats.items()},
            "consensus": consensus, "holders": holders,
            "universes": {u: [{k: w[k] for k in ("address", "score", "month_roi", "trades_30d", "max_dd", "account")}
                              for w in ws] for u, ws in ucats.items()},
            "consensus_A": consensus_a, "holders_A": holders_a, "consensus_B": consensus_b, "holders_B": holders_b}
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(OUT)
    log_event("TopTraders", "crypto_copy", "INFO", "", f"{len(kept)} wallets reguliers retenus sur {CANDIDATES} candidats ; "
              f"univers A {len(ucats['A'])} wallets ({len(consensus_a)} coins), univers B {len(ucats['B'])} wallets "
              f"({len(consensus_b)} coins), consensus global {len(consensus)} coins")
    return data


_started = False


def start(every_s: int = 3600) -> None:
    global _started
    if _started or not int(os.environ.get("TOP_TRADERS_ENABLED", 1)):
        return
    _started = True

    def loop():
        time.sleep(120)
        while True:
            try:
                run()
            except Exception as e:
                log_event("TopTraders", "crypto_copy", "INFO", "", f"erreur {type(e).__name__}: {str(e)[:150]}")
            time.sleep(every_s)

    threading.Thread(target=loop, daemon=True, name="top_traders").start()
