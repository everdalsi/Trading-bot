"""Rolling IC monitor per agent (trading v3, V5) -- runs on the PC inside the Trading Floor server.

Question it answers: does each agent's opinion actually predict the next price move? Only agents whose
information coefficient (IC) is positive and stable earn a weight (user rule: measure before trusting).

Every hour: snapshot of every agent's current signal per coin, from the read-only copies of the bot's files
(dashboard/cache/): ChartAnalyst score (1h and 4h), SentimentAnalyst score, TopTraders consensus, Polymarket
sentiment, MacroAnalyst score (market-wide). Later, the forward return of each coin at +4 h and +24 h
(Binance 1h candles, public) is attached, and per agent and horizon:
  IC   = mean over snapshots of the Spearman rank correlation, across coins, between signal and forward return
         (Qlib-style cross-sectional IC; the macro score is market-wide: time-series correlation with BTC instead)
  ICIR = mean IC / std IC (stability), n = number of snapshots used.
Verdict: IC > 0.02 and ICIR > 0.3 over >= 8 weeks of snapshots -> "predictive"; IC < 0 -> "contrarian or noise".
Files: cache/signal_snapshots.jsonl (one line per hour), cache/ic_monitor.json (result, read by server.py).
"""
import json
import time
import urllib.request
from pathlib import Path
from statistics import mean, pstdev

CACHE = Path(__file__).resolve().parent / "cache"
SNAP = CACHE / "signal_snapshots.jsonl"
OUT = CACHE / "ic_monitor.json"
HORIZONS = {"4h": 4, "24h": 24}
MIN_COINS = 5


def _json(name: str) -> dict:
    try:
        return json.loads((CACHE / name).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _base(sym: str) -> str:
    return sym[:-4] if sym.endswith("USDT") else sym


def snapshot() -> dict:
    """Current signal of every agent per coin (USDT symbols)."""
    sig: dict[str, dict[str, float]] = {}

    def put(agent: str, sym: str, v) -> None:
        if v is None:
            return
        try:
            sig.setdefault(agent, {})[sym if sym.endswith("USDT") else sym + "USDT"] = float(v)
        except (TypeError, ValueError):
            pass

    charts = _json("chart_analysis.json").get("symbols") or {}
    for sym, tfs in charts.items():
        for tf in ("1h", "4h"):
            put(f"ChartAnalyst_{tf}", sym, (tfs.get(tf) or {}).get("score"))
    for sym, c in (_json("sentiment.json").get("coins") or {}).items():
        put("SentimentAnalyst", sym, c.get("score"))
    for sym, v in (_json("top_traders.json").get("consensus") or {}).items():
        put("TopTraders", sym, v.get("consensus") if isinstance(v, dict) else v)
    for sym, c in (_json("polymarket.json").get("coins") or {}).items():
        put("Polymarket", sym, c.get("sentiment"))
    macro = _json("macro.json").get("score")
    if macro is not None:
        put("MacroAnalyst", "BTCUSDT", macro)
    return {"at": time.time(), "signals": sig}


def record() -> None:
    snap = snapshot()
    if snap["signals"]:
        with SNAP.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snap) + "\n")


_klines: dict[str, tuple[float, list]] = {}


def _closes(sym: str) -> list[tuple[int, float]]:
    """(open time in s, close) of the last 1000 hourly candles, cached 30 min."""
    hit = _klines.get(sym)
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    url = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1h&limit=1000"
    with urllib.request.urlopen(url, timeout=15) as r:
        rows = [(int(k[0] // 1000), float(k[4])) for k in json.loads(r.read())]
    _klines[sym] = (time.time(), rows)
    return rows


def _price_at(rows: list[tuple[int, float]], t: float) -> float | None:
    """Close of the candle containing t (hourly)."""
    for ts, c in reversed(rows):
        if ts <= t:
            return c if t - ts < 3600 else None
    return None


def _rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = _rank(a), _rank(b)
    ma, mb = mean(ra), mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else None


def compute() -> dict:
    snaps = []
    if SNAP.exists():
        for line in SNAP.read_text(encoding="utf-8").splitlines():
            try:
                snaps.append(json.loads(line))
            except ValueError:
                pass
    now = time.time()
    res: dict[str, dict] = {}
    for hname, h in HORIZONS.items():
        ready = [s for s in snaps if now - s["at"] >= h * 3600 + 600]
        ics: dict[str, list[float]] = {}
        ts_pairs: dict[str, list[tuple[float, float]]] = {}
        for s in ready:
            for agent, per in s["signals"].items():
                pairs = []
                for sym, v in per.items():
                    try:
                        rows = _closes(sym)
                    except Exception:
                        continue
                    p0, p1 = _price_at(rows, s["at"]), _price_at(rows, s["at"] + h * 3600)
                    if p0 and p1:
                        pairs.append((v, p1 / p0 - 1))
                if agent == "MacroAnalyst":
                    ts_pairs.setdefault(agent, []).extend(pairs)
                elif len(pairs) >= MIN_COINS:
                    ic = spearman([p[0] for p in pairs], [p[1] for p in pairs])
                    if ic is not None:
                        ics.setdefault(agent, []).append(ic)
        for agent, xs in ts_pairs.items():
            if len(xs) >= 10:
                ic = spearman([p[0] for p in xs], [p[1] for p in xs])
                if ic is not None:
                    ics.setdefault(agent, []).append(ic)   # one time-series IC (market-wide signal)
        for agent, xs in ics.items():
            m = mean(xs)
            sd = pstdev(xs) if len(xs) > 1 else 0.0
            icir = m / sd if sd else None
            weeks = (now - min(s["at"] for s in ready)) / (7 * 86400) if ready else 0
            verdict = ("predictif" if m > 0.02 and (icir or 0) > 0.3 and weeks >= 8 else
                       "prometteur (trop tot)" if m > 0.02 else "contrarien ou bruit" if m < 0 else "neutre")
            res.setdefault(agent, {})[hname] = {"ic": round(m, 4), "icir": None if icir is None else round(icir, 2),
                                                "n": len(xs), "verdict": verdict}
    out = {"at": now, "snapshots": len(snaps), "first": snaps[0]["at"] if snaps else None, "agents": res}
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


if __name__ == "__main__":
    record()
    print(json.dumps(compute(), indent=1, ensure_ascii=False))
