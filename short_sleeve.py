"""Third PAPER portfolio "court terme" (spec: ~/.claude/plans/trading-court-terme.md, 2026-11-05).

Own ledger (DATA_DIR/meme_sim.json, $1,000 virtual), never touches `sim` (MICRO) nor the two
other portfolios. Simulation only: no wallet, no key, no signed transaction, ever.

Lane A "momentum alts" (70 % of capital): top-50 Binance USDT coins, once a day (00:05 UTC) keep
the 5 best 14-day returns that are > 0 and > BTC's; out of the top 5 or r14 < 0 -> sell; trailing
stop -12 % from the peak (checked every minute); no new entry while BTC < its 50-day average.
Costs: 0.10 % fee + 0.20 % slippage per side.

Lane M "memecoins" (30 %): OBSERVATION ONLY for now (spec §4.1: two weeks of recorded signals
before any trade). Every 5 min it looks at DexScreener pairs, applies every anti-rug filter
(RugCheck for Solana, GoPlus for EVM chains), and when a clean token shows the volume breakout it
records the would-be entry, then the price 1 h / 6 h / 24 h / 48 h later, keyed by chain + pair
address (never by symbol), in DATA_DIR/meme_observations.jsonl for the replay.

Risk (both lanes): 2 % (A) / 1 % (M) of equity per position, max 5 / 3 positions, -3 %/day and
-8 %/week stop new entries, circuit breaker (stop everything + Telegram) on 20 % drawdown from the
peak, 10 losses in a row, stale prices > 5 min, or a position that cannot be priced.

Switch: SHORT_SLEEVE_ENABLED=1 (default 0). Lane M trading would need SHORT_LANE_M_TRADING=1, which
this file refuses until the validation protocol is met (see _lane_m_trading_allowed).
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from agent_events import log_event  # decision journal, never raises

log = logging.getLogger("short_sleeve")

ENABLED = int(os.environ.get("SHORT_SLEEVE_ENABLED", 0))
STARTING_CASH = float(os.environ.get("SHORT_SLEEVE_STARTING_CASH", 1000.0))
DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
LEDGER_PATH = DATA_DIR / "meme_sim.json"
OBS_PATH = DATA_DIR / "meme_observations.jsonl"
LOCK = threading.Lock()
BINANCE = "https://api.binance.com"
DEX = "https://api.dexscreener.com"

# lane A
# 2026-11-05 user request (paper): ~100 % invested, spread at 5 % per position (up to 20 positions, the
# best momentum first). Lane M stays observation-only, so lane A gets the whole capital.
A_SHARE, A_POS_PCT, A_MAX_POS = 1.0, 0.05, 20
A_TOP_N, A_LOOKBACK, A_TRAIL = 50, 14, 0.12
A_HYST = float(os.environ.get("SHORT_A_HYST", 0.01))  # regime switch only 1 % beyond BTC's MA50
A_COST = 0.0010 + 0.0020          # fee + slippage, each side
A_MIN_AGE_DAYS = 90
STABLES = {"USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "USDD", "PYUSD", "EUR", "TRY", "BRL",
           "AEUR", "EURI", "USDE", "XUSD", "BFUSD", "RLUSD", "USD1", "WBTC", "WBETH", "BETH", "PAXG"}
# lane M (observation)
M_POS_PCT, M_MAX_POS = 0.01, 3
M_MIN_AGE_H, M_MIN_LIQ, M_MAX_TOP10, M_MAX_TAX = 24, 100_000, 0.30, 0.05
M_VOL_MULT, M_MAX_PUMP_1H = 3.0, 25.0
M_FOLLOW_H = (1, 6, 24, 48)
# risk
DAY_LOSS, WEEK_LOSS, MAX_DD, MAX_LOSS_STREAK, STALE_S = 0.03, 0.08, 0.20, 10, 300
TRADE_ID_BASE = 95_000_000        # far from MICRO (~35k) and HL copy (90M) trade ids


# ---------------------------------------------------------------- ledger

def _default() -> dict:
    return {"cash": STARTING_CASH, "starting_cash": STARTING_CASH, "positions": {}, "trades": [],
            "peak_equity": STARTING_CASH, "day": "", "day_start": STARTING_CASH, "week": "",
            "week_start": STARTING_CASH, "loss_streak": 0, "halted": "", "last_rotation": "",
            "last_price_ok": time.time(), "next_id": TRADE_ID_BASE}


def load() -> dict:
    with LOCK:
        try:
            data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
            for k, v in _default().items():
                data.setdefault(k, v)
            return data
        except (OSError, ValueError):
            return _default()


def save(ledger: dict) -> None:
    with LOCK:
        tmp = LEDGER_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(ledger, indent=1), encoding="utf-8")
        tmp.replace(LEDGER_PATH)


def _price_a(symbols: list[str]) -> dict[str, float]:
    r = requests.get(f"{BINANCE}/api/v3/ticker/price", timeout=10)
    r.raise_for_status()
    want = set(symbols)
    return {d["symbol"]: float(d["price"]) for d in r.json() if d["symbol"] in want}


def equity(ledger: dict, prices: dict[str, float] | None = None) -> float:
    total = ledger["cash"]
    for p in ledger["positions"].values():
        px = (prices or {}).get(p["symbol"], p.get("last_price", p["entry"]))
        if p.get("side") == "SHORT":
            total += p["usd"] + p["qty"] * (p["entry"] - px)  # collateral + unrealised short PnL
        else:
            total += p["qty"] * px
    return total


def _open(ledger: dict, lane: str, symbol: str, price: float, usd: float, cost: float, extra: dict,
          side: str = "LONG") -> None:
    usd = min(usd, ledger["cash"])
    if usd < 5:
        return
    # fee + slippage on entry: a long buys higher, a (simulated) short sells lower
    fill = price * (1 + cost) if side == "LONG" else price * (1 - cost)
    qty = usd / fill
    tid = ledger["next_id"]; ledger["next_id"] += 1
    ledger["cash"] -= usd
    ledger["positions"][f"{lane}:{symbol}"] = {
        "id": tid, "lane": lane, "symbol": symbol, "entry": fill, "qty": qty, "usd": usd, "side": side,
        "peak": price, "last_price": price, "opened": time.time(), **extra}
    log.info(f"[SHORT] {lane} {side} {symbol} ${usd:.2f} @ {price:.6g}")
    log_event("Executor", "court_terme", "BUY" if side == "LONG" else "SELL", symbol, f"voie {lane} {side}",
              score=extra.get("r14"), result="ouvert", price=price, usd=round(usd, 2), lane=lane, side=side)


def _close(ledger: dict, key: str, price: float, cost: float, reason: str, send_fn) -> float:
    p = ledger["positions"].pop(key)
    if p.get("side") == "SHORT":  # buy back higher by the cost: fee + slippage on exit too
        pnl = p["qty"] * (p["entry"] - price * (1 + cost))
        proceeds = p["usd"] + pnl
    else:
        proceeds = p["qty"] * price * (1 - cost)  # fee + slippage on exit too
        pnl = proceeds - p["usd"]
    ledger["cash"] += proceeds
    ledger["trades"].append({"id": p["id"], "lane": p["lane"], "symbol": p["symbol"], "entry": p["entry"],
                             "exit": price, "usd": p["usd"], "pnl": round(pnl, 4),
                             "pnl_pct": round(pnl / p["usd"] * 100, 2), "reason": reason,
                             "opened": p["opened"], "closed": time.time()})
    ledger["loss_streak"] = 0 if pnl > 0 else ledger["loss_streak"] + 1
    send_fn(f"{'✅' if pnl > 0 else '❌'} COURT TERME {p['lane']} {p['symbol']} ${pnl:+.2f} ({reason})")
    log_event("Executor", "court_terme", "CLOSE", p["symbol"], reason, result=round(pnl, 4), lane=p["lane"])
    return pnl


# ---------------------------------------------------------------- risk

def _risk(ledger: dict, eq: float, send_fn) -> bool:
    """Update day/week/peak marks; True when NEW entries are allowed. Trips the breaker if needed."""
    now = datetime.now(timezone.utc)
    day, week = now.strftime("%Y-%m-%d"), now.strftime("%G-W%V")
    if ledger["day"] != day:
        ledger["day"], ledger["day_start"] = day, eq
    if ledger["week"] != week:
        ledger["week"], ledger["week_start"] = week, eq
    ledger["peak_equity"] = max(ledger["peak_equity"], eq)
    why = ""
    if eq < ledger["peak_equity"] * (1 - MAX_DD):
        why = f"drawdown {1 - eq / ledger['peak_equity']:.0%} depuis le plus haut"
    elif ledger["loss_streak"] >= MAX_LOSS_STREAK:
        why = f"{ledger['loss_streak']} pertes d'affilee"
    elif time.time() - ledger["last_price_ok"] > STALE_S:
        why = "prix absents depuis plus de 5 min"
    if why and not ledger["halted"]:
        ledger["halted"] = why
        log_event("CircuitBreaker", "court_terme", "HALT", "", why, result="portefeuille arrete")
        send_fn(f"🛑 COURT TERME : coupe-circuit ({why}). Portefeuille arrete, rien ne s'ouvre plus. "
                f"Reprise manuelle uniquement (halted='' dans meme_sim.json apres analyse).")
    if ledger["halted"]:
        return False
    if eq < ledger["day_start"] * (1 - DAY_LOSS) or eq < ledger["week_start"] * (1 - WEEK_LOSS):
        return False
    return True


# ---------------------------------------------------------------- lane A

def _daily_closes(symbol: str, n: int = 100) -> list[float]:
    r = requests.get(f"{BINANCE}/api/v3/klines", params={"symbol": symbol, "interval": "1d", "limit": n}, timeout=10)
    r.raise_for_status()
    return [float(k[4]) for k in r.json()[:-1]]  # last candle still open: dropped


_coin_perps = {"at": 0.0, "set": set()}


def coin_perps() -> set[str]:
    """USDT-M perpetuals whose underlying is a crypto COIN (Binance futures exchangeInfo), cached 6 h.
    Excludes tokenised stocks / gold (GOOGLBUSDT, PAXG...) and matches where a short is possible."""
    if time.time() - _coin_perps["at"] > 6 * 3600 or not _coin_perps["set"]:
        try:
            d = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=20).json()
            _coin_perps["set"] = {x["symbol"] for x in d["symbols"] if x.get("underlyingType") == "COIN"
                                  and x.get("contractType") == "PERPETUAL" and x.get("status") == "TRADING"}
            _coin_perps["at"] = time.time()
        except Exception:
            pass  # keep the last set (empty -> no filter)
    return _coin_perps["set"]


def universe() -> list[str]:
    r = requests.get(f"{BINANCE}/api/v3/ticker/24hr", timeout=15)
    r.raise_for_status()
    rows = [d for d in r.json() if d["symbol"].endswith("USDT")]
    def ok(sym):
        base = sym[:-4]
        return base not in STABLES and not base.endswith(("UP", "DOWN", "BULL", "BEAR"))
    perps = coin_perps()
    rows = [d for d in rows if ok(d["symbol"]) and (not perps or d["symbol"] in perps)]
    rows.sort(key=lambda d: float(d["quoteVolume"]), reverse=True)
    return [d["symbol"] for d in rows[:A_TOP_N]]


def lane_a_targets() -> tuple[list[str], bool, dict[str, float]]:
    """(top-5 symbols, BTC above MA50, r14 per symbol)."""
    btc = _daily_closes("BTCUSDT")
    btc_r = btc[-1] / btc[-1 - A_LOOKBACK] - 1
    try:  # single RegimeAgent (regime.py): same rule, close > MA50
        import regime
        btc_ok = regime.current()["above_ma50"]
    except Exception:
        btc_ok = btc[-1] > sum(btc[-50:]) / 50
    r14 = {}
    for s in universe():
        try:
            c = _daily_closes(s)
        except requests.RequestException:
            continue
        if len(c) < A_MIN_AGE_DAYS:  # younger than ~90 days of history
            continue
        r14[s] = c[-1] / c[-1 - A_LOOKBACK] - 1
    ranked = sorted((s for s, r in r14.items() if r > 0 and r > btc_r and s != "BTCUSDT"),
                    key=lambda s: r14[s], reverse=True)
    # mirror for risk off (user request 2026-11-05, ~100 % invested in both regimes): the weakest coins,
    # 14-day return < 0 and < BTC's, shorted
    weak = sorted((s for s, r in r14.items() if r < 0 and r < btc_r and s != "BTCUSDT"), key=lambda s: r14[s])
    lane_a_targets.weak = weak[:A_MAX_POS]
    return ranked[:A_MAX_POS], btc_ok, r14


def lane_a_rotate(ledger: dict, can_open: bool, send_fn) -> None:
    longs, btc_ok, r14 = lane_a_targets()
    # Direction set by the regime, never against it, with hysteresis: switch side only when BTC closes
    # more than A_HYST beyond its MA50 (a flip costs ~2 x A_COST on the whole lane).
    held = {p.get("side", "LONG") for p in ledger["positions"].values() if p["lane"] == "A"}
    prev = ledger.get("a_side") or (held.pop() if len(held) == 1 else ("LONG" if btc_ok else "SHORT"))
    side = prev
    try:
        import regime
        st = regime.current()
        if prev == "LONG" and st["close"] < st["ma50"] * (1 - A_HYST):
            side = "SHORT"
        elif prev == "SHORT" and st["close"] > st["ma50"] * (1 + A_HYST):
            side = "LONG"
    except Exception:
        side = "LONG" if btc_ok else "SHORT"
    if side != prev:
        log_event("RegimeAgent", "court_terme", "SIGNAL", "BTC", f"bascule voie A {prev} -> {side} (MM50 +/-{A_HYST:.0%})",
                  result=side)
    ledger["a_side"] = side
    btc_ok = side == "LONG"
    targets = longs if side == "LONG" else getattr(lane_a_targets, "weak", [])
    log_event("RegimeAgent", "court_terme", "SIGNAL", "BTC", "BTC au-dessus de sa MM50" if btc_ok else "BTC sous sa MM50",
              result="risk_on" if btc_ok else "risk_off")
    log_event("SignalAgent", "court_terme", "SIGNAL", ",".join(targets),
              "momentum 14 j : les plus forts (achat)" if side == "LONG" else "momentum 14 j : les plus faibles (vente a decouvert)",
              result={s: round(r14[s], 4) for s in targets})
    prices = _price_a(list({*targets, *(p["symbol"] for p in ledger["positions"].values() if p["lane"] == "A")}))
    for key, p in list(ledger["positions"].items()):
        wrong = p.get("side", "LONG") != side
        faded = r14.get(p["symbol"], -1) < 0 if p.get("side", "LONG") == "LONG" else r14.get(p["symbol"], 1) > 0
        if p["lane"] == "A" and (wrong or p["symbol"] not in targets or faded):
            _close(ledger, key, prices.get(p["symbol"], p["last_price"]), A_COST, "rotation", send_fn)
    if not can_open:
        log.info(f"[SHORT] A: no new entries (risk limits, BTC>MA50={btc_ok})")
        return
    eq = equity(ledger, prices)
    budget_left = eq * A_SHARE - sum(p["usd"] for p in ledger["positions"].values() if p["lane"] == "A")
    for s in targets:
        if f"A:{s}" in ledger["positions"] or sum(p["lane"] == "A" for p in ledger["positions"].values()) >= A_MAX_POS:
            continue
        usd = min(eq * A_POS_PCT, budget_left)
        try:  # ChartAnalyst: no LONG entry less than 0.5 ATR under a major 4 h resistance (spec C)
            import chart_analyst
            if side == "LONG" and chart_analyst.near_resistance(s, "4h", 0.5):
                log_event("ChartAnalyst", "court_terme", "VETO", s, "a moins de 0,5 ATR sous une resistance 4 h",
                          result="entree refusee")
                continue
        except Exception:
            pass
        if s in prices and usd >= 5:
            _open(ledger, "A", s, prices[s], usd, A_COST, {"r14": round(r14[s], 4)}, side=side)
            budget_left -= usd


def lane_a_watch(ledger: dict, send_fn) -> dict[str, float]:
    syms = [p["symbol"] for p in ledger["positions"].values() if p["lane"] == "A"]
    if not syms:
        ledger["last_price_ok"] = time.time()
        return {}
    prices = _price_a(syms)
    if len(prices) < len(set(syms)):
        missing = set(syms) - set(prices)
        ledger["halted"] = ledger["halted"] or f"position introuvable : {', '.join(missing)}"
        send_fn(f"🛑 COURT TERME : coupe-circuit, prix introuvable pour {', '.join(missing)}")
        return prices
    ledger["last_price_ok"] = time.time()
    for key, p in list(ledger["positions"].items()):
        if p["lane"] != "A":
            continue
        px = prices[p["symbol"]]
        if p.get("side") == "SHORT":  # best price of a short = lowest; stop when it bounces A_TRAIL above it
            p["last_price"], p["peak"] = px, min(p["peak"], px)
            if px >= p["peak"] * (1 + A_TRAIL):
                _close(ledger, key, px, A_COST, f"stop suiveur short +{A_TRAIL:.0%}", send_fn)
        else:
            p["last_price"], p["peak"] = px, max(p["peak"], px)
            if px <= p["peak"] * (1 - A_TRAIL):
                _close(ledger, key, px, A_COST, f"stop suiveur -{A_TRAIL:.0%}", send_fn)
    return prices


# ---------------------------------------------------------------- lane M (observation)

def _rugcheck_ok(mint: str) -> tuple[bool, str]:
    r = requests.get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", timeout=15)
    if r.status_code != 200:
        return False, f"rugcheck {r.status_code}"
    d = r.json()
    if d.get("mintAuthority") or d.get("freezeAuthority"):
        return False, "mint/freeze non revoques"
    # top 10 holders in % of supply, without the pool vaults (spec: "hors LP")
    pools = set((d.get("knownAccounts") or {}).keys())
    for m in d.get("markets") or []:
        pools |= {v for k in ("pubkey", "liquidityA", "liquidityB", "liquidityAAccount", "liquidityBAccount")
                  if isinstance(v := m.get(k), str)}
    holders = [h for h in d.get("topHolders") or [] if h.get("address") not in pools and h.get("owner") not in pools]
    top = sum(float(h.get("pct", 0)) for h in holders[:10]) / 100
    if top > M_MAX_TOP10:
        return False, f"top10 {top:.0%}"
    lp_locked = max((float(m.get("lp", {}).get("lpLockedPct", 0) or 0) for m in d.get("markets") or []), default=0)
    if lp_locked < 90:
        return False, f"LP verrouillee {lp_locked:.0f}%"
    fee = float((d.get("transferFee") or {}).get("pct") or 0) / 100
    if fee >= M_MAX_TAX:
        return False, f"taxe {fee:.0%}"
    if d.get("rugged"):
        return False, "rugged"
    if any(str(x.get("level", "")).lower() == "danger" for x in d.get("risks") or []):
        return False, "risque 'danger' rugcheck"
    return True, "ok"


_GOPLUS_CHAIN = {"ethereum": "1", "bsc": "56", "base": "8453", "arbitrum": "42161", "polygon": "137"}


def _goplus_ok(chain: str, addr: str) -> tuple[bool, str]:
    cid = _GOPLUS_CHAIN.get(chain)
    if not cid:
        return False, f"chaine {chain} non couverte"
    r = requests.get(f"https://api.gopluslabs.io/api/v1/token_security/{cid}",
                     params={"contract_addresses": addr}, timeout=15)
    d = (r.json().get("result") or {}).get(addr.lower()) or {}
    if not d:
        return False, "goplus vide"
    if d.get("is_honeypot") == "1" or d.get("cannot_sell_all") == "1":
        return False, "honeypot"
    tax = max(float(d.get("buy_tax") or 0), float(d.get("sell_tax") or 0))
    if tax >= M_MAX_TAX:
        return False, f"taxe {tax:.0%}"
    if d.get("is_mintable") == "1" or d.get("owner_change_balance") == "1":
        return False, "mint/owner actif"
    holders = d.get("holders") or []
    top = sum(float(h.get("percent", 0)) for h in holders[:10] if not h.get("is_contract") == 1)
    if top > M_MAX_TOP10:
        return False, f"top10 {top:.0%}"
    lp = d.get("lp_holders") or []
    locked = sum(float(h.get("percent", 0)) for h in lp if h.get("is_locked") == 1
                 or str(h.get("address", "")).lower().startswith("0x000000000000000000000000000000000000dead"))
    if locked < 0.9:
        return False, f"LP verrouillee {locked:.0%}"
    return True, "ok"


def _candidates() -> list[dict]:
    """Pairs of recently boosted / promoted tokens on DexScreener (one request each)."""
    seen, out = set(), []
    for url in (f"{DEX}/token-boosts/latest/v1", f"{DEX}/token-boosts/top/v1"):
        try:
            for t in requests.get(url, timeout=15).json()[:40]:
                key = (t.get("chainId"), t.get("tokenAddress"))
                if all(key) and key not in seen:
                    seen.add(key)
                    out.append({"chain": key[0], "token": key[1]})
        except (requests.RequestException, ValueError):
            continue
    return out


def _best_pair(chain: str, token: str) -> dict | None:
    r = requests.get(f"{DEX}/tokens/v1/{chain}/{token}", timeout=15)
    pairs = r.json() if r.status_code == 200 else []
    pairs = [p for p in pairs if (p.get("liquidity") or {}).get("usd")]
    return max(pairs, key=lambda p: p["liquidity"]["usd"]) if pairs else None


def _pair_price(chain: str, pair: str) -> float | None:
    r = requests.get(f"{DEX}/latest/dex/pairs/{chain}/{pair}", timeout=15)
    ps = (r.json() or {}).get("pairs") or [] if r.status_code == 200 else []
    return float(ps[0]["priceUsd"]) if ps and ps[0].get("priceUsd") else None


def lane_m_observe() -> int:
    """Record clean volume breakouts (no trade). Returns how many were recorded."""
    obs = _load_obs()
    tracked = {o["pair"] for o in obs if time.time() - o["at"] < 48 * 3600}
    n = 0
    for c in _candidates():
        try:
            p = _best_pair(c["chain"], c["token"])
        except (requests.RequestException, ValueError):
            continue
        if not p or p["pairAddress"] in tracked:
            continue
        liq = float(p["liquidity"]["usd"])
        age_h = (time.time() * 1000 - float(p.get("pairCreatedAt") or 0)) / 3.6e6
        vol = p.get("volume") or {}
        chg = p.get("priceChange") or {}
        h1, h24 = float(vol.get("h1") or 0), float(vol.get("h24") or 0)
        breakout = h24 > 0 and h1 > M_VOL_MULT * h24 / 24
        not_after_peak = 0 < float(chg.get("h1") or 0) < M_MAX_PUMP_1H
        # DexScreener gives no 6 h high: "price above the 6 h high" approximated by h6 > 0 and h1 > 0
        above_6h = float(chg.get("h6") or 0) > 0
        if age_h < M_MIN_AGE_H or liq < M_MIN_LIQ or not (breakout and not_after_peak and above_6h):
            continue
        # shared SecurityAgent (veto, fail-closed): DexScreener + RugCheck / GoPlus + honeypot.is
        import security_agent
        ok, reasons, _sec = security_agent.check(c["chain"], c["token"], "court_terme")
        why = "ok" if ok else " ; ".join(reasons)[:200]
        rec = {"at": time.time(), "chain": c["chain"], "token": c["token"], "pair": p["pairAddress"],
               "symbol": (p.get("baseToken") or {}).get("symbol"), "price": float(p["priceUsd"]), "liq": liq,
               "age_h": round(age_h, 1), "vol_h1": h1, "vol_h24": h24, "chg_h1": chg.get("h1"),
               "filters_ok": ok, "filters": why, "follow": {}}
        _append_obs(rec)
        log_event("MemeScout", "court_terme", "OBSERVE", rec["symbol"] or "", why, score=round(h1 / max(1, h24 / 24), 2),
                  result="propre" if ok else "rejete", chain=c["chain"], pair=p["pairAddress"])
        n += 1
    return n


def lane_m_follow() -> None:
    """Price 1/6/24/48 h after each recorded signal, by pair address (for the replay)."""
    obs = _load_obs()
    changed = False
    for o in obs:
        for h in M_FOLLOW_H:
            k = f"{h}h"
            if k not in o["follow"] and time.time() - o["at"] >= h * 3600:
                try:
                    o["follow"][k] = _pair_price(o["chain"], o["pair"])
                except (requests.RequestException, ValueError):
                    o["follow"][k] = None
                changed = True
    if changed:
        with LOCK:
            OBS_PATH.write_text("".join(json.dumps(o) + "\n" for o in obs), encoding="utf-8")


def _load_obs() -> list[dict]:
    try:
        return [json.loads(line) for line in OBS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def _append_obs(rec: dict) -> None:
    with LOCK:
        with open(OBS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


def _lane_m_trading_allowed() -> bool:
    """Lane M stays observation-only until the spec's validation protocol is met and the user agrees.
    The env switch alone is not enough on purpose."""
    return False


# ---------------------------------------------------------------- loop and reporting

def tick(send_fn, now: datetime | None = None) -> None:
    ledger = load()
    try:
        prices = lane_a_watch(ledger, send_fn)
    except requests.RequestException as e:
        log.warning(f"[SHORT] price error: {e}")
        prices = {}
    can_open = _risk(ledger, equity(ledger, prices), send_fn)
    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    # daily rotation; plus an hourly retry while lane A is empty (startup, or a day with no target)
    empty_a = not any(p["lane"] == "A" for p in ledger["positions"].values())
    perps = coin_perps()
    off = perps and any(p["lane"] == "A" and p["symbol"] not in perps for p in ledger["positions"].values())
    retry = (empty_a or off) and time.time() - ledger.get("last_rotation_try", 0) >= 3600
    if not ledger["halted"] and (ledger["last_rotation"] != today or retry) and (now.hour, now.minute) >= (0, 5):
        try:
            ledger["last_rotation_try"] = time.time()
            lane_a_rotate(ledger, can_open, send_fn)
            ledger["last_rotation"] = today
        except requests.RequestException as e:
            log.warning(f"[SHORT] rotation error: {e}")
    save(ledger)


def _loop(send_fn) -> None:
    last_m = 0.0
    while True:
        try:
            tick(send_fn)
            if time.time() - last_m >= 300:
                lane_m_observe()
                lane_m_follow()
                last_m = time.time()
        except Exception as e:  # never kill the thread
            log.warning(f"[SHORT] loop error: {type(e).__name__}: {e}")
        time.sleep(60)


_started = False


def start(send_fn) -> None:
    global _started
    if not ENABLED or _started:
        return
    _started = True
    threading.Thread(target=_loop, args=(send_fn,), daemon=True, name="short_sleeve").start()
    log.info("[SHORT] court terme sleeve started (paper, lane M observation only)")


def summary() -> dict:
    ledger = load()
    pos = ledger["positions"]
    closed = ledger["trades"]
    wins = sum(t["pnl"] > 0 for t in closed)
    gains = sum(t["pnl"] for t in closed if t["pnl"] > 0)
    losses = -sum(t["pnl"] for t in closed if t["pnl"] < 0)
    return {"total": round(equity(ledger), 2), "cash": round(ledger["cash"], 2),
            "invested": round(sum(p["usd"] if p.get("side") == "SHORT" else p["qty"] * p.get("last_price", p["entry"])
                                 for p in pos.values()), 2),
            "starting_cash": ledger["starting_cash"], "open_positions": {k.split(":", 1)[1]: v["lane"] for k, v in pos.items()},
            "realized_pnl": round(sum(t["pnl"] for t in closed), 2), "closed_trades": len(closed),
            "win_rate": round(wins / len(closed) * 100, 1) if closed else 0.0,
            "profit_factor": round(gains / losses, 2) if losses else None,
            "halted": ledger["halted"], "observations": len(_load_obs())}


def telegram_block() -> str:
    s = summary()
    status = "🟢 actif" if ENABLED else "⏸ désactivé"
    if s["halted"]:
        status = f"🛑 arrêté ({s['halted']})"
    pos = ", ".join(s["open_positions"]) or "aucune"
    return (f"⚡ COURT TERME (momentum alts + memecoins en observation, {status})\n"
            f"💰 Total : ${s['total']:,.2f} (départ ${s['starting_cash']:,.0f})\n"
            f"📈 En trade : ${s['invested']:,.2f} | 💵 Cash libre : ${s['cash']:,.2f}\n"
            f"📍 Positions ({len(s['open_positions'])}) : {pos}\n"
            f"✅ PnL réalisé : ${s['realized_pnl']:+,.2f} | PF : {s['profit_factor'] or '-'} "
            f"({s['closed_trades']} clôturés) | 👀 signaux memecoins observés : {s['observations']}")
