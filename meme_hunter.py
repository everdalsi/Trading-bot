"""MemeHunter (B2 of trading-agents-v2.md + V7 of trading-agents-v3.md), portfolio 3 memecoins.
MEASUREMENT MODE: no paper dollar is committed until >= 500 candidates are logged (V7: survivorship
bias) and the user agrees. Public DexScreener data, nothing signed, no wallet.

Every 15 s: DexScreener latest token profiles + latest/top boosts (Solana, Base, BSC, Ethereum).
EVERY candidate is logged once in DATA_DIR/meme_candidates.jsonl, including the rejected ones:
  - market filters: liquidity >= 100 k$, 5-min volume >= 10 k$, buys/sells 5 min >= 1.2, age >= 1 h,
    price change 5 min < +40 % (not after the spike);
  - SecurityAgent verdict (veto, fail-closed) for candidates that pass the market filters;
  - decision: "trade" (would buy), "rien" (filters or veto), with detection -> decision latency.
Follow-up price at +5 min, +1 h, +24 h, +7 d for every candidate (batched, 30 tokens per request), so the
hit rate x2 and the false-veto rate are measured on ALL candidates, not only the traded ones.
Shadow trades for "trade" decisions: 10 $ (1 % of the 1000 $ sleeve) bought at detection price + DEX cost
(fee 0.3 % + slippage size/liquidity + token tax, min 1 %), half sold at x2, the rest on a 30 % trailing
stop from the peak, hard stop -40 %, time stop 24 h; prices checked every minute. Recorded in
DATA_DIR/meme_hunter.json with the summary (candidates, verdicts, hit rate x2, shadow net PnL).

V2 (2026-11-12, user request, still measurement mode, every threshold is an env variable to tune later):
  - creator check: veto when the creator still holds more than MEME_MAX_CREATOR_PCT of the supply;
  - momentum trigger: a "trade" needs a breakout above the high of the previous hour (GeckoTerminal 5-min
    candles) OR a 5-min volume >= MEME_MIN_VOL_ACCEL times the average of the rest of the hour. Candidates
    that pass every safety check without momentum become the control group (decision "temoin"): same 10 $
    shadow trade with the ORIGINAL exits only, so the trigger and the new exits can be judged against it;
  - smart exits for momentum trades: "volume retombe" (5-min volume down MEME_FADE_DROP from the entry level
    on FADE_CONFIRM consecutive checks, after MEME_FADE_MIN_HOLD_S) and "le createur vend" (creator's share
    down MEME_DEV_DUMP_DROP since entry, re-read every DEV_RECHECK_S).
"""
import json
import os
import threading
import time
from pathlib import Path

import requests

from agent_events import log_event

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
CANDS = DATA_DIR / "meme_candidates.jsonl"
OUT = DATA_DIR / "meme_hunter.json"
DEX = "https://api.dexscreener.com"
CHAINS = {"solana", "base", "bsc", "ethereum"}
def _env(name: str, default: float) -> float:
    return float(os.environ.get(name) or default)


MIN_LIQ, MIN_VOL_M5 = _env("MEME_MIN_LIQ", 100_000), _env("MEME_MIN_VOL_M5", 10_000)
MIN_BUY_RATIO, MIN_AGE_H, MAX_PUMP_M5 = 1.2, _env("MEME_MIN_AGE_H", 1.0), 40.0
FOLLOW = {"5m": 300, "1h": 3600, "24h": 86400, "7d": 7 * 86400}
SHADOW_USD, DEX_FEE, MIN_COST = 10.0, 0.003, 0.01
TP_X, TRAIL, HARD_STOP, TIME_STOP = 2.0, 0.30, 0.40, 86400
MIN_CANDIDATES_FOR_PAPER = 500
GECKO = "https://api.geckoterminal.com/api/v2"
GECKO_NET = {"solana": "solana", "base": "base", "bsc": "bsc", "ethereum": "eth"}
BREAKOUT_CANDLES, MIN_BREAKOUT_CANDLES = 12, 6  # 12 x 5 min = the previous hour
MAX_CREATOR_PCT = _env("MEME_MAX_CREATOR_PCT", 0.15)
MIN_VOL_ACCEL = _env("MEME_MIN_VOL_ACCEL", 3.5)
FADE_DROP, FADE_MIN_HOLD, FADE_CONFIRM = _env("MEME_FADE_DROP", 0.60), _env("MEME_FADE_MIN_HOLD_S", 900), 3
DEV_DUMP_DROP, DEV_MIN_PCT, DEV_RECHECK_S = _env("MEME_DEV_DUMP_DROP", 0.40), 0.005, 300
_LOCK = threading.Lock()


def _trend_rank(symbol: str):
    """CoinGecko trending rank (Desktop trending.py), measurement only; None if not trending / no data."""
    try:
        import trending
        return trending.rank(symbol)
    except Exception:
        return None


def _get(url: str):
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def _load() -> list[dict]:
    try:
        return [json.loads(x) for x in CANDS.read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        return []


def _save(rows: list[dict]) -> None:
    with _LOCK:
        tmp = CANDS.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        tmp.replace(CANDS)


def discover() -> list[tuple[str, str]]:
    """(chain, token) of the latest profiles and boosts, supported chains only."""
    out, seen = [], set()
    for path in ("/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"):
        try:
            for t in _get(DEX + path)[:60]:
                key = (t.get("chainId"), t.get("tokenAddress"))
                if key[0] in CHAINS and key[1] and key not in seen:
                    seen.add(key)
                    out.append(key)
        except Exception:
            continue
    return out


def pairs_for(chain: str, tokens: list[str]) -> dict[str, dict]:
    """token -> its most liquid pair (one request per 30 tokens)."""
    best = {}
    for i in range(0, len(tokens), 30):
        try:
            rows = _get(f"{DEX}/tokens/v1/{chain}/{','.join(tokens[i:i + 30])}")
        except Exception:
            continue
        for p in rows or []:
            tok = ((p.get("baseToken") or {}).get("address") or "")
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
            key = next((t for t in tokens[i:i + 30] if t.lower() == tok.lower()), None)
            if key and liq > float(((best.get(key) or {}).get("liquidity") or {}).get("usd") or -1):
                best[key] = p
    return best


def market_filters(p: dict) -> list[str]:
    liq = float((p.get("liquidity") or {}).get("usd") or 0)
    vol5 = float((p.get("volume") or {}).get("m5") or 0)
    tx = (p.get("txns") or {}).get("m5") or {}
    buys, sells = float(tx.get("buys") or 0), float(tx.get("sells") or 0)
    age_h = (time.time() * 1000 - float(p.get("pairCreatedAt") or 0)) / 3.6e6
    chg5 = float((p.get("priceChange") or {}).get("m5") or 0)
    bad = []
    if liq < MIN_LIQ:
        bad.append(f"liquidite {liq:,.0f} $")
    if vol5 < MIN_VOL_M5:
        bad.append(f"volume 5 min {vol5:,.0f} $")
    if buys / max(1.0, sells) < MIN_BUY_RATIO:
        bad.append(f"achats/ventes 5 min {buys:.0f}/{sells:.0f}")
    if age_h < MIN_AGE_H:
        bad.append(f"age {age_h:.1f} h")
    if chg5 >= MAX_PUMP_M5:
        bad.append(f"deja +{chg5:.0f} % en 5 min")
    return bad


def vol_accel(p: dict) -> float | None:
    """5-min volume divided by the 5-min average of the rest of the hour; None when that hour was empty."""
    vol = p.get("volume") or {}
    m5, h1 = float(vol.get("m5") or 0), float(vol.get("h1") or 0)
    base = (h1 - m5) / 11
    return round(m5 / base, 2) if base > 0 else None


def is_breakout(candles: list, price: float) -> bool | None:
    """candles: newest first, [ts, open, high, low, close, volume]. True when price is above the high of the
    previous hour (the candle still forming is ignored); None when there is not enough history."""
    prior = candles[1:1 + BREAKOUT_CANDLES]
    if len(prior) < MIN_BREAKOUT_CANDLES:
        return None
    return price > max(float(c[2]) for c in prior)


def breakout_1h(chain: str, pair: str, price: float) -> bool | None:
    """None = unknown (chain not covered or GeckoTerminal unavailable): the volume trigger still applies."""
    net = GECKO_NET.get(chain)
    if not net or not pair:
        return None
    try:
        data = _get(f"{GECKO}/networks/{net}/pools/{pair}/ohlcv/minute?aggregate=5&limit={BREAKOUT_CANDLES + 1}")
        return is_breakout(data["data"]["attributes"]["ohlcv_list"], price)
    except Exception:
        return None


def momentum_trigger(accel: float | None, breakout: bool | None) -> str:
    """Reason to trade now, '' when the candidate has no momentum."""
    parts = ["cassure du plus haut 1 h"] if breakout else []
    if accel is not None and accel >= MIN_VOL_ACCEL:
        parts.append(f"volume x{accel:.1f}")
    return " + ".join(parts)


def fade_count(previous: int, entry_vol: float, vol_m5: float) -> int:
    """Consecutive checks with the 5-min volume down FADE_DROP from the entry level (0 = not fading)."""
    faded = entry_vol > 0 and vol_m5 <= entry_vol * (1 - FADE_DROP)
    return previous + 1 if faded else 0


def creator_dumped(entry_pct: float | None, now_pct: float | None) -> bool:
    if not entry_pct or entry_pct < DEV_MIN_PCT or now_pct is None:
        return False
    return now_pct <= entry_pct * (1 - DEV_DUMP_DROP)


def scan() -> int:
    t0 = time.time()
    rows = _load()
    known = {(r["chain"], r["token"].lower()) for r in rows}
    found = [(c, t) for c, t in discover() if (c, t.lower()) not in known]
    by_chain: dict[str, list[str]] = {}
    for c, t in found:
        by_chain.setdefault(c, []).append(t)
    n = 0
    for chain, toks in by_chain.items():
        pairs = pairs_for(chain, toks)
        for tok in toks:
            p = pairs.get(tok)
            rec = {"at": time.time(), "chain": chain, "token": tok, "follow": {}}
            if not p or not p.get("priceUsd"):
                rec.update(decision="rien", why="aucune paire cotee")
                rows.append(rec)
                n += 1
                continue
            rec.update(pair=p.get("pairAddress"), symbol=(p.get("baseToken") or {}).get("symbol"),
                       price=float(p["priceUsd"]), liq=float((p.get("liquidity") or {}).get("usd") or 0),
                       vol_m5=float((p.get("volume") or {}).get("m5") or 0),
                       age_h=round((time.time() * 1000 - float(p.get("pairCreatedAt") or 0)) / 3.6e6, 1))
            rec["vol_accel"] = vol_accel(p)  # logged for EVERY quoted candidate: lets the trigger be tuned later
            bad = market_filters(p)
            rec["market_ok"] = not bad
            if not bad:
                try:
                    import security_agent
                    # the hunter's own age and liquidity thresholds (SecurityAgent's default of 24 h vetoed
                    # 18 candidates out of 18 in the first week: the hunter targets pairs from 1 h old)
                    ok, reasons, det = security_agent.check(chain, tok, "memes", min_age_h=MIN_AGE_H, min_liq_usd=MIN_LIQ)
                except Exception as e:
                    ok, reasons, det = False, [f"securite indisponible {type(e).__name__}"], {}
                creator = det.get("creator_pct")
                rec.update(security_ok=ok, security=" ; ".join(reasons)[:200], tax=det.get("tax"), creator_pct=creator)
                bad = [] if ok else ["veto securite"]
                if ok and creator is not None and creator > MAX_CREATOR_PCT:
                    bad = [f"createur detient {creator:.0%} > {MAX_CREATOR_PCT:.0%}"]
            trigger = ""
            if not bad:
                breakout = breakout_1h(chain, rec.get("pair") or "", rec["price"])
                trigger = momentum_trigger(rec["vol_accel"], breakout)
                rec.update(breakout_1h=breakout, trigger=trigger)
            rec["decision"] = "rien" if bad else "trade" if trigger else "temoin"
            rec["why"] = " ; ".join(bad)[:200] if bad else trigger or "controles passes, sans elan (groupe temoin)"
            rec["latency_s"] = round(time.time() - t0, 1)
            if not bad:
                cost = max(MIN_COST, DEX_FEE + SHADOW_USD / max(1.0, rec["liq"]) + float(rec.get("tax") or 0))
                rec["shadow"] = {"entry": rec["price"] * (1 + cost), "cost": cost, "usd": SHADOW_USD, "peak": rec["price"],
                                 "half_sold": False, "realized": 0.0, "open": True, "control": not trigger,
                                 "vol_m5_entry": rec["vol_m5"], "fade_n": 0,
                                 "creator_pct": rec.get("creator_pct"), "creator_at": time.time()}
            if rec["decision"] == "trade":
                log_event("MemeHunter", "memes", "SIGNAL", rec["symbol"] or tok[:8],
                          f"{chain} {trigger}, liquidite {rec['liq']:,.0f} $, volume 5 min {rec['vol_m5']:,.0f} $ : achat fictif 10 $ (mode mesure)",
                          result="trade (ombre)", latency_s=rec["latency_s"], pair=rec.get("pair"),
                          trending=_trend_rank(rec.get("symbol") or ""))
            rows.append(rec)
            n += 1
    if n:
        _save(rows[-20000:])
    return n


def _quotes(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """(chain, token) -> its most liquid pair, quoted pairs only."""
    by_chain: dict[str, list[str]] = {}
    for r in rows:
        by_chain.setdefault(r["chain"], []).append(r["token"])
    out = {}
    for chain, toks in by_chain.items():
        for tok, p in pairs_for(chain, list(dict.fromkeys(toks))).items():
            if p.get("priceUsd"):
                out[(chain, tok.lower())] = p
    return out


def _smart_exit(r: dict, sh: dict, pair: dict, now: float) -> str:
    """V2 exits, momentum trades only (the control group keeps the original exits). Updates the shadow's
    fade counter and creator reading; returns the exit reason or ''."""
    if sh.get("control"):
        return ""
    vol_m5 = float((pair.get("volume") or {}).get("m5") or 0)
    sh["fade_n"] = fade_count(int(sh.get("fade_n") or 0), float(sh.get("vol_m5_entry") or 0), vol_m5)
    if (sh.get("creator_pct") or 0) >= DEV_MIN_PCT and now - float(sh.get("creator_at") or 0) >= DEV_RECHECK_S:
        import security_agent
        sh["creator_at"] = now
        sh["creator_now"] = security_agent.creator_pct(r["chain"], r["token"])
        if creator_dumped(sh["creator_pct"], sh["creator_now"]):
            return "le createur vend"
    if now - r["at"] >= FADE_MIN_HOLD and sh["fade_n"] >= FADE_CONFIRM:
        return f"volume retombe de {FADE_DROP:.0%}"
    return ""


def follow() -> None:
    """Follow-up prices for all candidates + shadow trade management (every minute)."""
    rows = _load()
    now = time.time()
    due = [r for r in rows if r.get("price") and (
        any(k not in r["follow"] and now - r["at"] >= s for k, s in FOLLOW.items()) or (r.get("shadow") or {}).get("open"))]
    if not due:
        return
    quotes = _quotes(due)
    for r in due:
        pair = quotes.get((r["chain"], r["token"].lower()))
        p = float(pair["priceUsd"]) if pair else None
        for k, s in FOLLOW.items():
            if k not in r["follow"] and now - r["at"] >= s:
                r["follow"][k] = p  # None = no quote any more (rugged / delisted)
        sh = r.get("shadow")
        if not sh or not sh.get("open"):
            continue
        if p is None:
            if now - r["at"] > 3600:  # no quote for over an hour: count as a total loss
                sh.update(open=False, exit_reason="plus de cotation", net=round(sh["realized"] - sh["usd"] * (0.5 if sh["half_sold"] else 1), 4))
            continue
        sh["peak"] = max(sh["peak"], p)
        left = 0.5 if sh["half_sold"] else 1.0
        exit_px = p * (1 - sh["cost"])
        if not sh["half_sold"] and p >= sh["entry"] * TP_X:
            sh["realized"] += 0.5 * sh["usd"] * exit_px / sh["entry"]
            sh["half_sold"] = True
            continue
        reason = ("stop -40 %" if p <= sh["entry"] * (1 - HARD_STOP) else
                  "stop suiveur 30 %" if sh["half_sold"] and p <= sh["peak"] * (1 - TRAIL) else
                  _smart_exit(r, sh, pair, now) or
                  ("temps 24 h" if now - r["at"] >= TIME_STOP else ""))
        if reason:
            sh["realized"] += left * sh["usd"] * exit_px / sh["entry"]
            sh.update(open=False, exit_reason=reason, net=round(sh["realized"] - sh["usd"], 4))
            log_event("MemeHunter", "memes", "CLOSE", r.get("symbol") or r["token"][:8], f"ombre : {reason}",
                      result=sh["net"], pair=r.get("pair"))
    _save(rows)


def summary() -> dict:
    rows = _load()
    priced = [r for r in rows if r.get("price")]

    def hit(rs, k):
        done = [r for r in rs if r["follow"].get(k) is not None or k in r["follow"]]
        x2 = [r for r in done if r["follow"].get(k) and r["follow"][k] >= 2 * r["price"]]
        return {"n": len(done), "x2": len(x2), "rate": round(len(x2) / len(done), 3) if done else None}

    def group(rs):
        done = [r for r in rs if not r["shadow"].get("open")]
        return {"n": len(rs), "closed": len(done), "net": round(sum(r["shadow"].get("net", 0) for r in done), 2)}

    shadows = [r for r in rows if r.get("shadow")]
    trades = [r for r in shadows if r.get("decision") == "trade"]
    closed = [r for r in trades if not r["shadow"].get("open")]
    vetoed = [r for r in priced if r.get("market_ok") and not r.get("security_ok")]
    lat = sorted(r.get("latency_s", 0) for r in priced)
    exits: dict[str, int] = {}
    for r in shadows:
        if r["shadow"].get("exit_reason"):
            exits[r["shadow"]["exit_reason"]] = exits.get(r["shadow"]["exit_reason"], 0) + 1
    return {"at": time.time(), "candidates": len(rows), "priced": len(priced),
            "market_ok": sum(1 for r in priced if r.get("market_ok")), "security_veto": len(vetoed),
            "creator_veto": sum(1 for r in priced if r.get("security_ok") and "createur detient" in r.get("why", "")),
            # V2: momentum trades vs the control group vs the trades taken before the trigger existed
            "by_group": {"elan": group([r for r in trades if "trigger" in r]),
                         # a control row whose breakout could not be read (GeckoTerminal down) is not a clean
                         # "no momentum" case: it is kept out of the comparison and counted below
                         "temoin": group([r for r in shadows if r.get("decision") == "temoin"
                                          and r.get("breakout_1h") is not None]),
                         "avant_v2": group([r for r in trades if "trigger" not in r])},
            "breakout_unknown": sum(1 for r in shadows if "trigger" in r and r.get("breakout_1h") is None),
            "creator_unknown": sum(1 for r in priced if r.get("security_ok") and r.get("creator_pct") is None),
            "exits": exits,
            "trade_signals": len(trades), "hit_x2_all_24h": hit(priced, "24h"), "hit_x2_traded_24h": hit(trades, "24h"),
            "hit_x2_vetoed_24h": hit(vetoed, "24h"),  # false-veto rate (V7)
            "shadow_closed": len(closed), "shadow_net": round(sum(r["shadow"].get("net", 0) for r in closed), 2),
            "latency_median_s": lat[len(lat) // 2] if lat else None,
            "paper_allowed": len(rows) >= MIN_CANDIDATES_FOR_PAPER and False}  # + user's approval: stays False


def _write_summary() -> None:
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(summary()), encoding="utf-8")
    tmp.replace(OUT)


_started = False


def start(scan_every_s: int = 15) -> None:
    global _started
    if _started or not int(os.environ.get("MEME_HUNTER_ENABLED", 1)):
        return
    _started = True

    def loop():
        time.sleep(60)
        last_follow = 0.0
        while True:
            try:
                scan()
                if time.time() - last_follow >= 60:
                    follow()
                    _write_summary()
                    last_follow = time.time()
            except Exception as e:
                log_event("MemeHunter", "memes", "INFO", "", f"erreur {type(e).__name__}: {str(e)[:150]}")
                time.sleep(30)
            time.sleep(scan_every_s)

    threading.Thread(target=loop, daemon=True, name="meme_hunter").start()
