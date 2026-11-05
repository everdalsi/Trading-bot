"""Trading room: live, read-only view of the trading bot's agents and portfolios.

    python dashboard\\server.py            -> http://127.0.0.1:3471

The bot runs on the VPS. A sync step (sync_from_vps(), every 30 s, read-only SFTP with an SSH key)
copies the small state files into dashboard/cache/; this server only reads that cache:
  - portfolio ledgers (crypto copy, US stocks, short-term) -> cash, equity, open positions, closed trades
  - agent_events.jsonl -> every agent decision (ts, agent, portfolio, action, symbol, reason, score, result)
Binds to 127.0.0.1 only. Never sends orders, never holds trading keys.
"""
import json
import os
import subprocess
import threading
import time
from datetime import datetime
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
CACHE = HERE / "cache"
PORT = 3471

# file in cache/ -> (portfolio id, label). Missing files are simply shown as "no data yet".
PORTFOLIOS = {
    "hl_copy": ("hl_copy_portfolio.json", "Crypto copie (Hyperliquid)"),
    "stocks": ("alpaca_stocks_portfolio.json", "Actions US (Alpaca)"),
    "short": ("meme_sim.json", "Court terme (momentum + memecoins)"),
}
EVENTS = CACHE / "agent_events.jsonl"
SYNC_STATE = CACHE / "_sync.json"


def _json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


VPS = "root@169.58.57.59"
KEY = Path.home() / ".ssh" / "id_ed25519_vps_readonly"   # forced-command key: can only read
LEDGER_FILES = {"hl_copy": "hl_copy_portfolio.json", "alpaca": "alpaca_stocks_portfolio.json", "short": "meme_sim.json",
                "hl_prices": "hl_copy_prices.json", "charts": "chart_analysis.json"}


def _ssh(cmd: str, timeout: int = 40) -> str:
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-i", str(KEY), VPS, cmd],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[-200:] or f"exit {out.returncode}")
    return out.stdout


def sync_from_vps() -> None:
    """Read-only pull of the ledgers and the decision journal into cache/ (every 30 s)."""
    state = {"at": time.time(), "error": ""}
    try:
        ledgers = json.loads(_ssh("ledgers"))
        for k, fname in LEDGER_FILES.items():
            if ledgers.get(k) is not None:
                (CACHE / fname).write_text(json.dumps(ledgers[k]), encoding="utf-8")
        for cmd, fname in (("events 300", "agent_events.jsonl"), ("micro", "micro.json"), ("status", "status.txt")):
            try:
                (CACHE / fname).write_text(_ssh(cmd), encoding="utf-8")
            except Exception:
                pass
    except Exception as e:
        state["error"] = str(e)[:200]
    SYNC_STATE.write_text(json.dumps(state), encoding="utf-8")


def _sync_loop() -> None:
    while True:
        sync_from_vps()
        time.sleep(30)


def _ts(v) -> float:
    """Epoch seconds from a number or an ISO / 'YYYY-MM-DD HH:MM:SS' string."""
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _num(*vals, default=0.0) -> float:
    for v in vals:
        try:
            if v is not None:
                return float(v)
        except (TypeError, ValueError):
            continue
    return default


def _portfolio(pid: str) -> dict:
    fname, label = PORTFOLIOS[pid]
    d = _json(CACHE / fname, None)
    if d is None:
        return {"id": pid, "label": label, "available": False}
    positions = d.get("positions") or d.get("open_positions") or {}
    if isinstance(positions, dict):
        positions = [{**v, "_key": k} for k, v in positions.items()]
    live = (_json(CACHE / "hl_copy_prices.json", {}) or {}).get("prices", {}) if pid == "hl_copy" else {}
    for pos in positions:
        if pos.get("_key") in live:
            pos["last_price"] = live[pos["_key"]]
    trades = d.get("trades") or d.get("closed_trades") or d.get("history") or []
    trades = [t for t in trades if isinstance(t, dict) and (t.get("pnl") is not None or t.get("pnl_usd") is not None)]
    cash = _num(d.get("cash"), d.get("virtual_cash"))
    def _value(p):
        usd = _num(p.get("amount_usd"), p.get("usd"), p.get("size_usd"), p.get("cost"), p.get("value"))
        entry, last = _num(p.get("price_in"), p.get("entry")), _num(p.get("last_price"))
        if entry and last:
            move = (last - entry) / entry
            return usd * (1 - move if str(p.get("side", "")).upper() == "SHORT" else 1 + move)
        return usd
    in_trade = sum(_value(p) for p in positions)
    start = _num(d.get("starting_cash"), d.get("start_cash"), default=1000.0)
    pnl_closed = sum(_num(t.get("pnl"), t.get("pnl_usd")) for t in trades)
    wins = sum(1 for t in trades if _num(t.get("pnl"), t.get("pnl_usd")) > 0)
    gross_win = sum(max(0, _num(t.get("pnl"), t.get("pnl_usd"))) for t in trades)
    gross_loss = -sum(min(0, _num(t.get("pnl"), t.get("pnl_usd"))) for t in trades)
    curve, eq = [], start
    for t in trades[-200:]:
        eq += _num(t.get("pnl"), t.get("pnl_usd"))
        curve.append(round(eq, 2))
    return {
        "id": pid, "label": label, "available": True,
        "equity": round(cash + in_trade, 2), "cash": round(cash, 2), "in_trade": round(in_trade, 2),
        "start": start, "pnl_closed": round(pnl_closed, 2), "n_trades": len(trades),
        "win_rate": round(100 * wins / len(trades), 1) if trades else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "halted": d.get("halted") or d.get("circuit_breaker") or "",
        "positions": [{
            "symbol": p.get("symbol") or p.get("ticker") or p.get("coin") or "?",
            "short": str(p.get("side", "")).upper() == "SHORT",
            "lane": p.get("lane", ""), "side": p.get("side", "long"),
            "entry": _num(p.get("price_in"), p.get("entry"), p.get("entry_price"), p.get("fill")),
            "last": _num(p.get("last_price"), p.get("current_price"), p.get("price"), p.get("current")),
            "usd": round(_num(p.get("amount_usd"), p.get("usd"), p.get("size_usd"), p.get("cost"), p.get("value")), 2),
            "opened": p.get("time_in") or p.get("opened_at") or p.get("ts"),
        } for p in positions][:30],
        "curve": curve,
    }


def _events(limit: int = 120) -> list[dict]:
    if not EVENTS.exists():
        return []
    lines = EVENTS.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    out = []
    for l in lines:
        try:
            out.append(json.loads(l))
        except Exception:
            continue
    return out[::-1]  # newest first


def _agents(events: list[dict]) -> list[dict]:
    """One card per agent seen in the journal: last action, decisions in the last hour."""
    now = time.time()
    by = defaultdict(list)
    for e in events:
        by[e.get("agent", "?")].append(e)
    cards = []
    for name, evs in by.items():
        last = evs[0]
        ts = _ts(last.get("ts"))
        hour = sum(1 for e in evs if now - _ts(e.get("ts")) < 3600)
        cards.append({"name": name, "portfolio": last.get("portfolio", ""), "last_action": last.get("action", ""),
                      "last_reason": last.get("reason", ""), "age_s": int(now - ts) if ts else None,
                      "active": bool(ts and now - ts < 300), "decisions_1h": hour,
                      "actions": dict(Counter(e.get("action", "?") for e in evs))})
    return sorted(cards, key=lambda c: (c["age_s"] is None, c["age_s"] or 0))


import re as _re


def _logs() -> dict:
    """Container state + what the bot's own agents last said (regime, soul, stocks cycle)."""
    try:
        txt = (CACHE / "status.txt").read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    lines = txt.splitlines()
    out = {"container": lines[0].strip() if lines else "", "log": [l for l in lines[1:] if l.strip()][-40:]}
    for l in reversed(lines):
        if "regime" not in out and (m := _re.search(r"Régime: (\w+) \| conf: (\d+)%", l)):
            out["regime"], out["regime_conf"] = m.group(1), int(m.group(2))
        if "soul" not in out and (m := _re.search(r"phase=(\w+) \| conf=(\d+)% \| kelly=([\d.]+)%", l)):
            out["soul"] = {"phase": m.group(1), "conf": int(m.group(2)), "kelly": float(m.group(3))}
        if "agents_total" not in out and (m := _re.search(r"(\d+) agents initialisés", l)):
            out["agents_total"] = int(m.group(1))
        if "pairs" not in out and (m := _re.search(r"(\d+) paires USDT actives", l)):
            out["pairs"] = int(m.group(1))
    return out


def _micro() -> dict:
    d = _json(CACHE / "micro.json", {})
    trades = d.get("last_trades") or []
    rows = []
    for t in trades[::-1][:25]:
        deb = t.get("debate") or {}
        rows.append({"symbol": t.get("symbol"), "side": t.get("side"), "usd": round(_num(t.get("amount_usd")), 2),
                     "pnl": _num(t.get("pnl")), "pnl_pct": _num(t.get("pnl_pct")), "conf": t.get("confidence"),
                     "reason": t.get("reason", ""), "exit": t.get("exit_reason", ""), "time": t.get("time_out") or t.get("time_in"),
                     "verdict": deb.get("verdict", ""), "bull": (deb.get("bull_case") or [""])[0],
                     "bear": (deb.get("bear_case") or [""])[0], "regime": (t.get("ohlc") or {}).get("chop_regime", ""),
                     "structure": (t.get("smc") or {}).get("structure", ""), "orderbook": t.get("ob_pressure", "")})
    pnls = [r["pnl"] for r in rows]
    return {"cash": round(_num(d.get("cash")), 2), "closed": d.get("closed_trades"), "realized": d.get("realized_pnl"),
            "open": d.get("open_positions"), "trades": rows,
            "recent_wr": round(100 * sum(1 for x in pnls if x > 0) / len(pnls), 1) if pnls else None}


def _stocks() -> dict:
    d = _json(CACHE / "alpaca_stocks_portfolio.json", {})
    latest = {}
    for x in d.get("decisions") or []:
        latest[x.get("symbol")] = x
    rows = sorted(latest.values(), key=lambda x: -abs(_num(x.get("score"))))[:16]
    return {"risk": d.get("risk_state") or {}, "last_run": d.get("last_run"),
            "signals": [{"symbol": x.get("symbol"), "action": x.get("action"), "score": x.get("score"),
                         "tech": x.get("technical_score"), "f13": x.get("13f_direction"), "congress": x.get("congress_direction"),
                         "ret5": x.get("ret_5d_pct"), "close": x.get("last_close"), "open": x.get("market_open")} for x in rows]}


def _wallets() -> list:
    d = _json(CACHE / "hl_copy_portfolio.json", {})
    w = d.get("wallets") or []
    return [{"addr": x.get("address", "")[:6] + "…" + x.get("address", "")[-4:], "value": x.get("account_value"),
             "roi_m": x.get("month_roi"), "roi_w": x.get("week_roi")} for x in sorted(w, key=lambda x: -_num(x.get("month_roi")))[:10]]



# The agency (plan ~/.claude/plans/trading-agence-pro.md): name in agent_events.jsonl -> (label, role, step)
ROSTER = [
    ("Coordinator", "Coordinateur", "répartit le capital entre portefeuilles, coupe ceux qui perdent", 2),
    ("SeniorExpert", "Expert senior", "veto si le gain espéré ne paie pas les frais", 1),
    ("Professor", "Professeur", "apprend de chaque trade, propose des réglages", 3),
    ("Auditor", "Auditeur", "n'adopte un changement que s'il bat l'ancien sur 200 trades", 3),
    ("CostAnalyst", "Analyste des coûts", "frais + glissement réels attendus", 1),
    ("Pruner", "Élagueur", "réduit ce qui perd par stratégie / symbole / régime", 2),
    ("SizingAgent", "Dimensionnement", "même risque par trade selon la volatilité", 2),
    ("RegimeAgent", "Régime de marché", "tendance / range / baisse", 0),
    ("ChartAnalyst", "Analyseur de courbes", "tendance, RSI, supports / résistances, figures", 1),
    ("SignalAgent", "Signal", "momentum et scores d'entrée", 0),
    ("DrawdownGuard", "Garde-fou drawdown", "veto si la perte devient trop forte", 0),
    ("CorrelationWatcher", "Surveillant corrélation", "réduit les positions trop liées", 0),
    ("Executor", "Exécuteur", "passe les ordres (fictifs)", 0),
    ("DataGuard", "Gardien des données", "bloque les entrées si un prix est faux ou figé", 1),
    ("Watchdog", "Veilleur technique", "santé du bot + alertes Telegram", 1),
    ("EventAgent", "Agent événements", "pause autour de la Fed, inflation, piratages", 4),
    ("DerivativesAnalyst", "Analyste dérivés", "funding, positions ouvertes, liquidations", 4),
    ("MemeScout", "Éclaireur memecoins", "observe les memecoins (sans trader)", 0),
]


def _roster(events: list[dict]) -> list[dict]:
    seen = {}
    for e in events:  # newest first
        seen.setdefault(e.get("agent"), e)
    now = time.time()
    out = []
    for key, label, role, step in ROSTER:
        e = seen.get(key)
        age = (now - e["ts"]) if e and e.get("ts") else None
        out.append({"key": key, "label": label, "role": role, "step": step,
                    "state": "live" if age is not None and age < 900 else "on" if e or step == 0 else "planned",
                    "last": ({"action": e.get("action"), "symbol": e.get("symbol"), "reason": e.get("reason"), "age_s": int(age)}
                             if e else None)})
    return out

def status() -> dict:
    events = _events()
    for e in events:
        e["ts"] = _ts(e.get("ts"))
    books = [_portfolio(p) for p in PORTFOLIOS]
    micro = _micro()
    return {"now": time.strftime("%H:%M:%S"), "sync": _json(SYNC_STATE, {}), "portfolios": books,
            "agents": _agents(events), "roster": _roster(events), "events": events[:80], "logs": _logs(), "micro": micro,
            "stocks": _stocks(), "wallets": _wallets(),
            "total_equity": round(sum(b.get("equity", 0) for b in books if b.get("available")) + _num(micro.get("cash")), 2)}



# ---------------------------------------------------------------- charts (public Binance candles, read-only)
import urllib.parse
import urllib.request

_kcache: dict = {}


def _klines(symbol: str, interval: str, limit: int = 300) -> list:
    key = (symbol, interval)
    if key in _kcache and time.time() - _kcache[key][0] < 30:
        return _kcache[key][1]
    q = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": limit})
    with urllib.request.urlopen(f"https://api.binance.com/api/v3/klines?{q}", timeout=15) as r:
        rows = json.loads(r.read())
    data = [{"time": int(k[0] / 1000), "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
             "close": float(k[4]), "volume": float(k[5])} for k in rows]
    _kcache[key] = (time.time(), data)
    return data


def _levels(c: list, side: str, n: int = 3) -> list:
    """Support/resistance: swing pivots (5 bars each side) clustered within 0.5 ATR, most touched first."""
    if len(c) < 30:
        return []
    trs = [max(c[i]["high"] - c[i]["low"], abs(c[i]["high"] - c[i - 1]["close"]), abs(c[i]["low"] - c[i - 1]["close"]))
           for i in range(1, len(c))]
    atr = sum(trs[-14:]) / 14
    key = "low" if side == "support" else "high"
    piv = [c[i][key] for i in range(5, len(c) - 5)
           if all((c[i][key] <= c[j][key]) if side == "support" else (c[i][key] >= c[j][key]) for j in range(i - 5, i + 6))]
    clusters: list = []
    for v in sorted(piv):
        if clusters and abs(v - clusters[-1]["px"]) <= 0.5 * atr:
            cl = clusters[-1]
            cl["px"] = (cl["px"] * cl["n"] + v) / (cl["n"] + 1)
            cl["n"] += 1
        else:
            clusters.append({"px": v, "n": 1})
    last = c[-1]["close"]
    cand = [x for x in clusters if (x["px"] < last if side == "support" else x["px"] > last)]
    return [round(x["px"], 8) for x in sorted(cand, key=lambda x: (-x["n"], abs(x["px"] - last)))[:n]]


def _marks(symbol: str) -> list:
    """Our entries / exits on this symbol from the ledgers (crypto copy)."""
    d = _json(CACHE / "hl_copy_portfolio.json", {})
    out = []
    pos = d.get("positions") or {}
    items = list(pos.values()) if isinstance(pos, dict) else pos
    for t in items + (d.get("trades") or []):
        if t.get("symbol") != symbol:
            continue
        if t.get("open_time") or t.get("time_in"):
            out.append({"t": _ts(t.get("open_time") or t.get("time_in")), "px": t.get("price_in"), "kind": "entry",
                        "side": t.get("side", "LONG")})
        if t.get("price_out") and t.get("time_out"):
            out.append({"t": _ts(t.get("time_out")), "px": t.get("price_out"), "kind": "exit", "pnl": t.get("pnl")})
    return out


def chart_data(symbol: str, interval: str) -> dict:
    c = _klines(symbol, interval)
    raw = _json(CACHE / "chart_analysis.json", {}) or {}
    analysis = (raw.get("symbols") or raw).get(symbol, {})
    return {"symbol": symbol, "interval": interval, "candles": c, "supports": _levels(c, "support"),
            "resistances": _levels(c, "resistance"), "marks": _marks(symbol), "analysis": analysis}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path in ("/", "/index.html"):
            return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if self.path.startswith("/chart"):
            return self._send(200, (HERE / "chart.html").read_bytes(), "text/html; charset=utf-8")
        if self.path.startswith("/api/chart"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            sym = (q.get("symbol", ["BTCUSDT"])[0] or "BTCUSDT").upper()
            itv = q.get("interval", ["1h"])[0]
            if itv not in ("5m", "15m", "1h", "4h", "1d") or not sym.isalnum():
                return self._send(400, b"bad request", "text/plain")
            try:
                return self._send(200, json.dumps(chart_data(sym, itv)).encode(), "application/json")
            except Exception as e:
                return self._send(502, json.dumps({"error": str(e)[:200]}).encode(), "application/json")
        if self.path.startswith("/api/status"):
            return self._send(200, json.dumps(status(), ensure_ascii=False).encode(), "application/json")
        self._send(404, b"not found", "text/plain")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    CACHE.mkdir(exist_ok=True)
    threading.Thread(target=_sync_loop, daemon=True).start()
    print(f"trading room on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
