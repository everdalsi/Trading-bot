"""Data / API keeper (technical agent, desk spec 06/11): connections, quotas, freshness, keys never in clear.

  check_all() -> {provider: {ok, ms, used_weight?, error?}}   one light call per provider (ping-like endpoints),
                 Binance used weight from its rate-limit header (1-min budget 6000): the desks slow down before a ban
  freshness()  -> {file: {age_s, expected_s, stale}}           every agent output file in DATA_DIR vs its period:
                 a stale file means its agent is dead or its source is down -> DataGuard must not trust it
  secrets_scan(paths) -> [findings]                             API keys / private keys written in clear in logs or
                 data files (they must live only in the secrets env file); findings are masked, never printed whole
  report() -> all three, written to DATA_DIR/api_keeper.json (start(600) = every 10 min)
"""
import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
UA = {"User-Agent": "trading-bot-paper/1.0"}
PROVIDERS = {
    "binance_spot": ("GET", "https://api.binance.com/api/v3/ping", None),
    "binance_futures": ("GET", "https://fapi.binance.com/fapi/v1/ping", None),
    "hyperliquid": ("POST", "https://api.hyperliquid.xyz/info", {"type": "meta"}),
    "coingecko": ("GET", "https://api.coingecko.com/api/v3/ping", None),
    "defillama": ("GET", "https://stablecoins.llama.fi/stablecoins?includePrices=false", None),
    "marginpad": ("GET", "https://marginpad.io/api/v1/long-short", None),
    "coinlobster": ("GET", "https://coinlobster.com/api/public/whale-radar", None),
    "optimistfi": ("GET", "https://optimistfi.com/api/perps/venues", None),
    "cryptovision": ("GET", "https://cryptocurrency.cv/api/news?limit=1", None),
    "deribit": ("GET", "https://www.deribit.com/api/v2/public/get_time", None),
    "polymarket_gamma": ("GET", "https://gamma-api.polymarket.com/markets?limit=1", None),
}
# agent output files and how often they should be rewritten (seconds)
EXPECTED = {"market_context.json": 300, "funding_carry.json": 300, "radar.json": 300, "regime_hmm.json": 3600,
            "trending.json": 900, "macro.json": 1800, "sentiment.json": 900, "options.json": 1800,
            "polymarket.json": 1800, "top_traders.json": 7200, "risk_manager.json": 300, "news_flags.json": 600,
            "chart_analysis.json": 300, "coordinator.json": 300}
KEY_PATTERNS = [re.compile(p) for p in (r"AKIA[0-9A-Z]{16}", r"-----BEGIN (RSA |EC )?PRIVATE KEY-----",
                                        r"\b(sk|pk)_(live|test)_[0-9a-zA-Z]{16,}", r"\b0x[a-fA-F0-9]{64}\b",
                                        r"(?i)(api[_-]?secret|secret[_-]?key|private[_-]?key)\W{0,3}[:=]\W{0,3}[A-Za-z0-9/+=_-]{20,}")]


def _call(method: str, url: str, body) -> tuple[bool, int, dict]:
    t0 = time.time()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={**UA, **({"Content-Type": "application/json"} if data else {})})
    with urllib.request.urlopen(req, timeout=12) as r:
        r.read(2048)
        return r.status < 400, int((time.time() - t0) * 1000), dict(r.headers)


def check_all() -> dict:
    out = {}
    for name, (m, url, body) in PROVIDERS.items():
        try:
            ok, ms, h = _call(m, url, body)
            row = {"ok": ok, "ms": ms}
            w = h.get("x-mbx-used-weight-1m") or h.get("X-MBX-USED-WEIGHT-1M")
            if w:
                row["used_weight_1m"] = int(w)
                row["weight_warning"] = int(w) > 4800                 # 80 % of Binance's 6000/min budget
            out[name] = row
        except Exception as e:
            out[name] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:80]}"}
    return out


def freshness() -> dict:
    now, out = time.time(), {}
    for name, period in EXPECTED.items():
        p = DATA_DIR / name
        if p.exists():
            age = now - p.stat().st_mtime
            out[name] = {"age_s": int(age), "expected_s": period, "stale": age > 3 * period}
        else:
            out[name] = {"missing": True}
    return out


def secrets_scan(paths: list[Path] | None = None, max_bytes: int = 2_000_000) -> list[dict]:
    paths = paths or [p for p in DATA_DIR.glob("*") if p.is_file() and p.suffix in (".json", ".jsonl", ".log", ".txt", ".md")]
    found = []
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")[:max_bytes]
        except Exception:
            continue
        for rx in KEY_PATTERNS:
            for m in rx.finditer(text):
                s = m.group(0)
                if s.startswith("0x") and len(s) == 66:
                    # 32-byte hex is also a tx hash or a pool / pair id (MemeHunter, 06/11 CLI report): count it
                    # only when a key word sits just before it
                    before = text[max(0, m.start() - 40):m.start()].lower()
                    if not any(w in before for w in ("private", "secret", "priv_key", "privkey", '"pk"', "pk=",
                                                     "pk:", "mnemonic", "seed")):
                        continue
                found.append({"file": p.name, "pattern": rx.pattern[:30], "masked": s[:4] + "..." + s[-2:]})
    return found


def report() -> dict:
    rep = {"at": time.time(), "providers": check_all(), "freshness": freshness(), "secrets_in_clear": secrets_scan()}
    rep["summary"] = {"providers_down": [k for k, v in rep["providers"].items() if not v.get("ok")],
                      "stale_files": [k for k, v in rep["freshness"].items() if v.get("stale")],
                      "secrets_found": len(rep["secrets_in_clear"])}
    try:
        (DATA_DIR / "api_keeper.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    except Exception:
        pass
    return rep


_thread = None


def start(every_s: int = 600) -> threading.Thread:
    global _thread
    if _thread is not None and _thread.is_alive():
        return _thread

    def loop():
        while True:
            try:
                report()
            except Exception:
                pass
            time.sleep(every_s)
    _thread = threading.Thread(target=loop, name="ApiKeeper", daemon=True)
    _thread.start()
    return _thread


if __name__ == "__main__":
    r = report()
    print(json.dumps(r["summary"], indent=1))
    print({k: v for k, v in r["providers"].items()})
