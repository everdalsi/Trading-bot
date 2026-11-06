"""Step 1 of the "agence de trading pro" plan (~/.claude/plans/trading-agence-pro.md), approved by
the user on 2026-11-05. PAPER only. Four agents, every decision written to agent_events.jsonl:

- CostAnalyst: expected round-trip cost per portfolio (fees + slippage measured on our own fills
  when we have them, else a stated assumption) and the fees / gross PnL ratio for the daily report.
- SeniorExpert: final gate before any NEW position. VETO when expected edge - costs <= 0.
  Expected edge = net historical expectancy of the same portfolio/signal once >= 100 closed trades;
  below that, a stated proxy (crypto copy: source wallet monthly ROI / 30; US stocks: score above
  the buy threshold x 0.5 %). Also vetoes thin liquidity and anything DataGuard blocks, and
  records the drawdown / correlation verdicts already taken, so one line explains the decision.
- DataGuard: blocks ENTRIES when prices are missing, frozen (> 2 min unchanged while BTC moves),
  incoherent (<= 0, or > 30 % jump vs the last price seen within 10 min) or the venue is down.
  Exits are never blocked.
- Watchdog: background thread, Telegram alerts (deduplicated 6 h): stale portfolio data, data
  guard blocking, unusual loss (> 5 % in 24 h), low disk; plus one daily summary at 20:00 UTC.
"""
import json
import os
import shutil
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from agent_events import PATH as EVENTS_PATH, log_event

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
BINANCE = "https://api.binance.com"
MIN_SAMPLE = 100
FEE_SIDE = {"crypto_copy": float(os.environ.get("FEE_PCT", 0.001)), "actions_us": 0.0}  # Alpaca: no commission
SLIP_ASSUMED = {"crypto_copy": 0.0005, "actions_us": 0.0005}  # per side, until measured on our fills
MIN_QUOTE_VOL_USD = float(os.environ.get("AGENCY_MIN_QUOTE_VOL", 5_000_000))
CHART_TREND_MALUS = float(os.environ.get("AGENCY_CHART_MALUS", 0.0025))  # 0.25 % of edge
LEDGERS = {"crypto_copy": DATA_DIR / "hl_copy_ledger.json", "actions_us": DATA_DIR / "alpaca_stocks_ledger.json"}


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _closed(portfolio: str) -> list[dict]:
    """Closed trades with pnl and pnl_pct. The US-stocks ledger keeps them in "closed_trades"
    without pnl_pct (computed here from cost); the crypto copy ledger in "trades"."""
    led = _load(LEDGERS[portfolio])
    rows = led.get("closed_trades") if "closed_trades" in led else led.get("trades", [])
    out = []
    for t in rows or []:
        if t.get("pnl") is None:
            continue
        if t.get("pnl_pct") is None:
            base = float(t.get("cost") or t.get("amount_usd") or 0)
            t = {**t, "pnl_pct": float(t["pnl"]) / base * 100 if base else 0.0}
        out.append(t)
    return out


# ---------------------------------------------------------------- CostAnalyst

def measured_slippage(portfolio: str) -> tuple[float | None, int]:
    """Median |fill - reference| / reference on our own entries that recorded a reference price."""
    led = _load(LEDGERS[portfolio])
    rows = list(led.get("positions", {}).values()) + led.get("trades", []) + led.get("closed_trades", [])
    samples = []
    for r in rows:
        ref, fill = r.get("ref_price"), r.get("entry_price") or r.get("price_in")
        if ref and fill:
            samples.append(abs(float(fill) - float(ref)) / float(ref))
    return (statistics.median(samples), len(samples)) if len(samples) >= 10 else (None, len(samples))


def round_trip_cost(portfolio: str) -> tuple[float, str]:
    slip, n = measured_slippage(portfolio)
    src = f"glissement mesure ({n} executions)" if slip is not None else "glissement suppose"
    slip = slip if slip is not None else SLIP_ASSUMED[portfolio]
    return 2 * (FEE_SIDE[portfolio] + slip), src


def fees_report(portfolio: str, since: float = 0.0) -> dict:
    """Fees paid vs gross PnL on closed trades (gross = net + estimated fees)."""
    cost, _ = round_trip_cost(portfolio)
    t = [x for x in _closed(portfolio) if _ts(x) >= since]
    fees = sum(float(x.get("amount_usd") or x.get("cost") or 0) * cost for x in t)
    net = sum(float(x["pnl"]) for x in t)
    gross = net + fees
    return {"trades": len(t), "net": round(net, 2), "fees": round(fees, 2), "gross": round(gross, 2),
            "fees_over_gross": round(fees / gross, 2) if gross > 0 else None}


def _ts(t: dict) -> float:
    for k in ("closed_at", "time_out", "exit_time"):
        v = t.get(k)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v).timestamp()
            except ValueError:
                pass
    return 0.0


# ---------------------------------------------------------------- DataGuard

class DataGuard:
    def __init__(self):
        self.last: dict[str, tuple[float, float, float]] = {}  # symbol -> (price, seen_at, changed_at)
        self.blocked_reason = ""
        self.lock = threading.Lock()

    def observe(self, prices: dict[str, float]) -> None:
        now = time.time()
        with self.lock:
            for s, p in prices.items():
                old = self.last.get(s)
                changed = now if not old or old[0] != p else old[2]
                self.last[s] = (p, now, changed)

    def stale_s(self, symbol: str) -> float:
        """Seconds this symbol's price has not moved while BTC kept moving (0 = fine)."""
        now = time.time()
        with self.lock:
            s, btc = self.last.get(symbol), self.last.get("BTCUSDT")
        if not s or not btc or now - btc[2] > 120:
            return 0.0
        return max(0.0, now - s[2] - 120)

    def venue_ok(self, portfolio: str) -> tuple[bool, str]:
        if portfolio != "crypto_copy":
            return True, ""
        try:
            requests.get(f"{BINANCE}/api/v3/ping", timeout=5).raise_for_status()
            return True, ""
        except requests.RequestException as e:
            return False, f"Binance injoignable ({type(e).__name__})"

    def entry_ok(self, portfolio: str, symbol: str, price: float | None) -> tuple[bool, str]:
        ok, why = self.venue_ok(portfolio)
        if not ok:
            return self._block(why)
        if not price or price <= 0:
            return self._block(f"{symbol} : prix absent ou nul")
        now = time.time()
        with self.lock:
            old = self.last.get(symbol)
            btc = self.last.get("BTCUSDT")
        if old and now - old[1] < 600 and abs(price - old[0]) / old[0] > 0.30:
            return self._block(f"{symbol} : saut de prix {abs(price - old[0]) / old[0]:.0%} incoherent")
        if old and portfolio == "crypto_copy" and now - old[2] > 120 and btc and now - btc[2] < 120:
            return self._block(f"{symbol} : prix fige depuis {int((now - old[2]) / 60)} min")
        stale = self.stale_inputs()
        if stale:
            return self._block(f"donnees de controle perimees : {', '.join(stale)} (api_keeper)")
        self.blocked_reason = ""
        return True, ""

    # control inputs an entry relies on: if Desktop's api_keeper sees them stale (> 3 x their period), no new entry
    CRITICAL = ("chart_analysis.json", "risk_manager.json")

    def stale_inputs(self) -> list[str]:
        """Critical files flagged stale in DATA_DIR/api_keeper.json (ignored if that report is itself > 30 min old)."""
        try:
            import json as _json
            import os as _os
            from pathlib import Path as _P
            f = _P(_os.environ.get("DATA_DIR") or ".") / "api_keeper.json"
            if time.time() - f.stat().st_mtime > 1800:
                return []
            fr = (_json.loads(f.read_text(encoding="utf-8")).get("freshness") or {})
            return [n for n in self.CRITICAL if (fr.get(n) or {}).get("stale")]
        except Exception:
            return []

    def _block(self, why: str) -> tuple[bool, str]:
        self.blocked_reason = why
        return False, why


data_guard = DataGuard()


# ---------------------------------------------------------------- SeniorExpert

_vol_cache: dict = {"at": 0.0, "vol": {}}


def _quote_volume(symbol: str) -> float | None:
    if time.time() - _vol_cache["at"] > 900:
        try:
            rows = requests.get(f"{BINANCE}/api/v3/ticker/24hr", timeout=15).json()
            _vol_cache.update(at=time.time(), vol={r["symbol"]: float(r["quoteVolume"]) for r in rows})
        except (requests.RequestException, ValueError, KeyError):
            return None
    return _vol_cache["vol"].get(symbol)


def expected_edge(portfolio: str, proxy: float) -> tuple[float, str, int]:
    """(edge as a fraction per trade, before costs; how it was obtained; sample size)."""
    t = _closed(portfolio)
    if len(t) >= MIN_SAMPLE:
        cost, _ = round_trip_cost(portfolio)
        net = statistics.mean(float(x.get("pnl_pct") or 0) / 100 for x in t)
        return net + cost, f"esperance historique nette sur {len(t)} trades", len(t)
    return proxy, f"proxy (echantillon {len(t)} < {MIN_SAMPLE})", len(t)


def approve_entry(portfolio: str, symbol: str, price: float | None, proxy_edge: float,
                  context: dict | None = None) -> tuple[bool, str]:
    """Final go / no-go for a NEW position. Logs one SeniorExpert line either way. Fail-closed:
    any unexpected error refuses the entry instead of reaching the trading loop."""
    try:
        return _approve_entry(portfolio, symbol, price, proxy_edge, context or {})
    except Exception as e:
        why = f"erreur interne {type(e).__name__}: {str(e)[:150]}"
        log_event("SeniorExpert", portfolio, "VETO", symbol, why, result="refuse (fail-closed)")
        return False, why


def _approve_entry(portfolio: str, symbol: str, price: float | None, proxy_edge: float,
                   context: dict) -> tuple[bool, str]:
    ok, why = data_guard.entry_ok(portfolio, symbol, price)
    if not ok:
        log_event("DataGuard", portfolio, "VETO", symbol, why, result="entree bloquee (sorties permises)")
        return False, why
    try:  # NewsAgent: no entry < 6 h after a strong headline AGAINST our side on this symbol
        import news_agent
        bad = news_agent.against(symbol, context.get("side", "LONG"), 6 * 3600)
        if bad:
            why = f"news forte contre une entree {context.get('side', 'LONG')} < 6 h : {bad[:120]}"
            log_event("SeniorExpert", portfolio, "VETO", symbol, why, result="refuse")
            return False, why
    except ImportError:
        pass
    if portfolio == "crypto_copy":
        vol = _quote_volume(symbol)
        if vol is not None and vol < MIN_QUOTE_VOL_USD:
            why = f"liquidite faible : {vol / 1e6:.1f} M$ echanges sur 24 h (< {MIN_QUOTE_VOL_USD / 1e6:.0f} M$)"
            log_event("SeniorExpert", portfolio, "VETO", symbol, why, result="refuse")
            return False, why
    cost, cost_src = round_trip_cost(portfolio)
    edge, edge_src, n = expected_edge(portfolio, proxy_edge)
    # ChartAnalyst: 4 h trend against our side (daily chart for stocks) = malus, logged for the A/B check
    malus = 0.0
    try:
        import chart_analyst
        trend = chart_analyst.trend_4h(symbol)
        side = context.get("side", "LONG")
        if (side == "LONG" and trend == "down") or (side == "SHORT" and trend == "up"):
            malus = CHART_TREND_MALUS
            edge -= malus
            edge_src += f", malus tendance 4 h contraire -{malus * 100:.2f} %"
    except Exception:
        pass
    context["chart_malus"] = malus
    net = edge - cost
    detail = (f"avantage espere {edge * 100:+.2f} % ({edge_src}) - couts {cost * 100:.2f} % ({cost_src}) "
              f"= {net * 100:+.2f} %")
    extra = {k: v for k, v in context.items() if k in ("size_reduction", "drawdown", "wallet_roi", "chart_malus", "side", "fill_mode")}
    if "score" in context:  # log_event already has its own score (net edge): the signal score goes apart
        extra["signal_score"] = context["score"]
    try:  # agents v2 (Desktop): macro / sentiment / fundamentals RECORDED with each decision, no weight yet
        import macro_analyst, sentiment_analyst
        extra.update({k: v for k, v in macro_analyst.read().items() if v is not None})
        extra.update({k: v for k, v in sentiment_analyst.read(symbol).items() if v is not None})
        try:
            import options_analyst
            extra.update({k: v for k, v in options_analyst.read(symbol).items() if v is not None})
        except Exception:
            pass
        if portfolio == "actions_us":
            import fundamental_analyst
            extra.update({k: v for k, v in fundamental_analyst.read(symbol).items() if v is not None})
    except Exception:
        pass
    try:  # Polymarket sentiment RECORDED next to each decision (not weighted yet: measured first)
        import polymarket_analyst
        pm = polymarket_analyst.sentiment(symbol)
        if pm and pm.get("sentiment") is not None:
            extra["polymarket"] = pm["sentiment"]
    except Exception:
        pass
    try:  # V8: bull/bear debate + 3-profile risk committee, rule-based (no LLM), RECORDED only
        extra.update(debate(symbol, context.get("side", "LONG"), net, extra))
    except Exception:
        pass
    if net <= 0:
        log_event("SeniorExpert", portfolio, "VETO", symbol, detail, score=round(net, 5), result="refuse", **extra)
        return False, detail
    log_event("SeniorExpert", portfolio, "APPROVE", symbol, detail, score=round(net, 5), result="accepte", **extra)
    return True, detail


def debate(symbol: str, side: str, net_edge: float, ctx: dict) -> dict:
    """V8 (trading-agents-v3.md, TradingAgents scheme without an LLM). Each available signal argues for
    or against THIS side; bull = sum of arguments for, bear = sum against (each in [0, 1]).
    Committee: aggressive votes yes if bull >= bear - 0.5, neutral if bull > bear, conservative if
    bull > bear + 0.5 and net edge > 0.2 %. Recorded with the decision, no weight until measured."""
    sign = 1 if side != "SHORT" else -1
    args = {}
    try:
        import chart_analyst
        for tf in ("1h", "4h"):
            t = chart_analyst._tf(symbol, tf).get("trend")
            if t in ("up", "down"):
                args[f"tendance_{tf}"] = 0.5 * sign * (1 if t == "up" else -1)
    except Exception:
        pass
    try:
        import trade_manager
        c = trade_manager.consensus(symbol)
        if c is not None:
            args["consensus_top_wallets"] = sign * c
    except Exception:
        pass
    for k, scale in (("sentiment", 1.0), ("macro_score", 1.0), ("polymarket", 1.0)):
        v = ctx.get(k)
        if isinstance(v, (int, float)):
            args[k] = sign * max(-1.0, min(1.0, float(v) * scale))
    try:
        import news_agent
        if news_agent.against(symbol, side, 6 * 3600):
            args["news_forte_contre"] = -1.0
    except Exception:
        pass
    bull = round(sum(v for v in args.values() if v > 0), 3)
    bear = round(-sum(v for v in args.values() if v < 0), 3)
    votes = {"agressif": bull >= bear - 0.5, "neutre": bull > bear, "prudent": bull > bear + 0.5 and net_edge > 0.002}
    return {"bull": bull, "bear": bear, "committee_yes": sum(votes.values()),
            "debate_args": {k: round(v, 3) for k, v in args.items()}}


# ---------------------------------------------------------------- Watchdog

class Watchdog:
    def __init__(self, send_fn):
        self.send = send_fn
        self.sent: dict[str, float] = {}
        self.last_summary = ""

    def _alert(self, key: str, msg: str) -> None:
        if time.time() - self.sent.get(key, 0) < 6 * 3600:
            return
        self.sent[key] = time.time()
        log_event("Watchdog", "systeme", "ALERT", "", msg)
        try:
            self.send(f"🩺 WATCHDOG : {msg}")
        except Exception:
            pass

    def check(self) -> None:
        now = time.time()
        prices = DATA_DIR / "hl_copy_prices.json"
        if prices.exists() and now - prices.stat().st_mtime > 20 * 60:
            self._alert("hl_stale", f"copie crypto : aucun suivi des prix depuis {int((now - prices.stat().st_mtime) / 60)} min")
        if data_guard.blocked_reason:
            self._alert("dataguard", f"entrees bloquees par DataGuard : {data_guard.blocked_reason}")
        free = shutil.disk_usage(DATA_DIR).free / 1e9
        if free < 2:
            self._alert("disk", f"disque presque plein ({free:.1f} Go libres)")
        for pf in LEDGERS:
            day = [t for t in _closed(pf) if _ts(t) > now - 86400]
            loss = -sum(float(t["pnl"]) for t in day if float(t["pnl"]) < 0)
            start = float(_load(LEDGERS[pf]).get("starting_cash") or 1000)
            if loss > 0.05 * start:
                self._alert(f"loss_{pf}", f"{pf} : pertes inhabituelles, -{loss:.0f} $ sur 24 h (> 5 % du capital)")
        utc = datetime.now(timezone.utc)
        if utc.hour == 20 and self.last_summary != utc.strftime("%Y-%m-%d"):
            self.last_summary = utc.strftime("%Y-%m-%d")
            self.daily_summary()

    def daily_summary(self) -> None:
        since = time.time() - 86400
        lines = ["📋 RÉSUMÉ DU JOUR (agence, papier)"]
        for pf in LEDGERS:
            r = fees_report(pf, since)
            ratio = f" | frais/brut {r['fees_over_gross']:.0%}" if r["fees_over_gross"] is not None else ""
            lines.append(f"• {pf} : {r['trades']} trades clos, net {r['net']:+.2f} $, frais estimés {r['fees']:.2f} ${ratio}")
        vetoes: dict[str, int] = {}
        try:
            for line in EVENTS_PATH.read_text(encoding="utf-8").splitlines()[-5000:]:
                e = json.loads(line)
                if e.get("action") == "VETO" and datetime.fromisoformat(e["ts"]).timestamp() > since:
                    vetoes[e["agent"]] = vetoes.get(e["agent"], 0) + 1
        except (OSError, ValueError, KeyError):
            pass
        lines.append("• vetos 24 h : " + (", ".join(f"{k} {v}" for k, v in vetoes.items()) or "aucun"))
        log_event("Watchdog", "systeme", "INFO", "", "resume quotidien envoye")
        try:
            self.send("\n".join(lines))
        except Exception:
            pass

    def loop(self) -> None:
        time.sleep(120)
        while True:
            try:
                self.check()
            except Exception as e:
                log_event("Watchdog", "systeme", "INFO", "", f"erreur interne {type(e).__name__}: {e}")
            time.sleep(300)


_started = False


def start_watchdog(send_fn) -> None:
    global _started
    if _started or not int(os.environ.get("AGENCY_WATCHDOG_ENABLED", 1)):
        return
    _started = True
    threading.Thread(target=Watchdog(send_fn).loop, daemon=True, name="agency_watchdog").start()
