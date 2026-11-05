"""FundamentalAnalyst (agents v2, B7) -- MEASUREMENT MODE: recorded next to every US decision, no weight yet.

Reason to exist: portfolio 1 (US stocks) is meant to trade on fundamentals + technicals + news (user spec), but
the live score is technical + 13F + Congress only: no agent reads the companies' own numbers.
Frame: SEC EDGAR XBRL company facts (official filings, free, public), refreshed once a week per ticker; never
trades, never vetoes. ETFs (no company facts) are skipped.
Function: DATA_DIR/fundamentals.json = {at, tickers: {T: {score -1..+1, revenue_yoy_pct, net_margin_pct, eps_fy,
pe, period}}} and a SIGNAL in agent_events when a ticker's fundamental score is computed for the first time or
moves by >= 0.4.

Score parts (each -1..+1, plain average of the available ones):
  growth   latest quarter revenue vs the same quarter a year earlier: +20 % -> +1, -10 % -> -1
  margin   latest quarter net margin: >= 20 % -> +1, <= 0 % -> -1
  value    trailing P/E from the last fiscal-year diluted EPS: < 15 -> +0.5, > 60 -> -0.5, loss -> -1
"""
import json
import os
import threading
import time
from datetime import date
from pathlib import Path

import requests

from agent_events import log_event

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "fundamentals.json"
UA = {"User-Agent": "TradingResearchBot/1.0 paper-trading research (contact: bot-admin@example.org)"}
REFRESH_S = 7 * 86400
ETFS = {"SPY", "IVV", "QQQ", "VOO", "VTI", "DIA", "IWM"}
FALLBACK = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "KO", "AXP", "PLTR", "UBER", "INTC", "TSM"]
REV_TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"]
_cik: dict[str, str] = {}


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


def cik(ticker: str) -> str | None:
    if not _cik:
        rows = requests.get("https://www.sec.gov/files/company_tickers.json", headers=UA, timeout=20).json()
        _cik.update({r["ticker"].upper(): str(r["cik_str"]).zfill(10) for r in rows.values()})
    return _cik.get(ticker.upper().replace(".", "-"))


def _days(r: dict) -> int:
    try:
        return (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days
    except Exception:
        return 0


def _series(facts: dict, tags: list[str], unit: str) -> list[dict]:
    """All rows of every tag: companies switch tags over the years (e.g. Revenues -> RevenueFromContract...),
    so stopping at the first tag found returns years-old figures. Same period in two tags: last tag wins."""
    rows = {}
    for t in tags:
        for r in facts.get("us-gaap", {}).get(t, {}).get("units", {}).get(unit) or []:
            rows[(r.get("start"), r.get("end"), r.get("form"))] = r
    return list(rows.values())


def analyse(ticker: str) -> dict | None:
    c = cik(ticker)
    if not c:
        return None
    facts = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{c}.json", headers=UA, timeout=30).json()["facts"]
    rev = [r for r in _series(facts, REV_TAGS, "USD") if 80 <= _days(r) <= 100 and r.get("form") in ("10-Q", "10-K")]
    ni = {r["end"]: r["val"] for r in _series(facts, ["NetIncomeLoss"], "USD") if 80 <= _days(r) <= 100}
    eps_fy = [r for r in _series(facts, ["EarningsPerShareDiluted"], "USD/shares")
              if 350 <= _days(r) <= 380 and r.get("form") == "10-K"]
    out, parts = {}, {}
    if rev:
        rev = sorted({r["end"]: r for r in rev}.values(), key=lambda r: r["end"])
        last = rev[-1]
        prior = [r for r in rev if 350 <= (date.fromisoformat(last["end"]) - date.fromisoformat(r["end"])).days <= 380]
        out["period"] = last["end"]
        if prior and prior[-1]["val"]:
            g = last["val"] / prior[-1]["val"] - 1
            out["revenue_yoy_pct"] = round(g * 100, 1)
            parts["growth"] = _clip(g / 0.20) if g >= 0 else _clip(g / 0.10)
        if last["end"] in ni and last["val"]:
            m = ni[last["end"]] / last["val"]
            out["net_margin_pct"] = round(m * 100, 1)
            parts["margin"] = -1.0 if m <= 0 else _clip(m / 0.20)
    if eps_fy:
        eps = sorted(eps_fy, key=lambda r: r["end"])[-1]["val"]
        out["eps_fy"] = eps
        try:
            q = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}", params={"range": "5d", "interval": "1d"},
                             headers=UA, timeout=15).json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
            if eps <= 0:
                parts["value"] = -1.0
            else:
                pe = q / eps
                out["pe"] = round(pe, 1)
                parts["value"] = 0.5 if pe < 15 else -0.5 if pe > 60 else 0.0
        except Exception:
            pass
    if not parts:
        return None
    out["score"] = round(sum(parts.values()) / len(parts), 3)
    out["parts"] = {k: round(v, 3) for k, v in parts.items()}
    out["at"] = time.time()
    return out


def universe() -> list[str]:
    led = _json(DATA_DIR / "alpaca_stocks_ledger.json")
    syms = {d.get("symbol") for d in led.get("decisions", [])[-300:] if isinstance(d, dict)}
    syms |= set((led.get("positions") or {}).keys())
    syms = {s for s in syms if s and s not in ETFS}
    return sorted(syms) or FALLBACK


def run(force: bool = False) -> dict:
    data = _json(OUT) or {"tickers": {}}
    tick = data.setdefault("tickers", {})
    for t in universe():
        old = tick.get(t)
        if old and not force and time.time() - float(old.get("at", 0)) < REFRESH_S:
            continue
        try:
            res = analyse(t)
        except Exception as e:
            res = None
            log_event("FundamentalAnalyst", "systeme", "INFO", t, f"SEC indisponible : {type(e).__name__}")
        time.sleep(0.3)                                   # SEC fair access: well under 10 requests/s
        if not res:
            continue
        tick[t] = res
        if old is None or abs(res["score"] - float(old.get("score", 0))) >= 0.4:
            log_event("FundamentalAnalyst", "actions_us", "SIGNAL", t,
                      f"fondamentaux {res['score']:+.2f} (CA {res.get('revenue_yoy_pct', '?')} % sur un an, marge "
                      f"{res.get('net_margin_pct', '?')} %, PER {res.get('pe', '?')})", score=res["score"])
    data["at"] = time.time()
    data["mode"] = "mesure (aucun poids)"
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    return data


def read(ticker: str) -> dict:
    """For the SeniorExpert / US scorer: the ticker's fundamental score (recorded, no weight)."""
    t = (_json(OUT).get("tickers") or {}).get(ticker) or {}
    return {"fundamental": t.get("score"), "revenue_yoy_pct": t.get("revenue_yoy_pct"), "pe": t.get("pe")}


_started = False


def start(every_s: int = 6 * 3600) -> None:
    global _started
    if _started or not int(os.environ.get("FUNDAMENTAL_ANALYST_ENABLED", 1)):
        return
    _started = True

    def loop():
        time.sleep(300)
        while True:
            try:
                run()        # only tickers older than 7 days are refreshed, so each loop is cheap
            except Exception as e:
                log_event("FundamentalAnalyst", "systeme", "INFO", "", f"erreur interne {type(e).__name__}: {str(e)[:150]}")
            time.sleep(every_s)

    threading.Thread(target=loop, daemon=True, name="fundamental_analyst").start()


if __name__ == "__main__":
    d = run(force=True)
    for t, v in sorted(d["tickers"].items(), key=lambda kv: -kv[1]["score"]):
        print(t, v["score"], v.get("revenue_yoy_pct"), v.get("net_margin_pct"), v.get("pe"), v.get("period"))
