"""Attention signal: CoinGecko trending coins (free, no key; found via the public-apis list, user 06/11).
MEASUREMENT MODE: no weight, never vetoes. Gives the short-term book and MemeHunter a "people are searching for
this coin right now" feature, recorded next to their decisions until it proves itself (>= 100 decisions).

    refresh()            -> dict    fetch now, write DATA_DIR/trending.json {at, coins[{rank, symbol, name, id, mcap_rank}]}
    rank(symbol)         -> int | None   1 = most searched; accepts "SOL" or "SOLUSDT"; reads the file (no network)
    start(every_s=900)   -> thread  refresh every 15 min (CoinGecko free tier allows it easily)
Fail-open: any error keeps the previous file; rank() returns None.
"""
import json
import os
import threading
import time
from pathlib import Path

import requests

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "trending.json"
URL = "https://api.coingecko.com/api/v3/search/trending"


def refresh() -> dict:
    try:
        items = requests.get(URL, timeout=15).json()["coins"]
        coins = [{"rank": i + 1, "symbol": c["item"]["symbol"].upper(), "name": c["item"]["name"],
                  "id": c["item"]["id"], "mcap_rank": c["item"].get("market_cap_rank")} for i, c in enumerate(items)]
        data = {"at": time.time(), "coins": coins}
        tmp = OUT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(OUT)
        return data
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:80]}"}


def rank(symbol: str) -> int | None:
    try:
        data = json.loads(OUT.read_text(encoding="utf-8"))
        if time.time() - data["at"] > 3 * 3600:          # stale: no opinion
            return None
        base = symbol.upper().removesuffix("USDT").removesuffix("USDC").removeprefix("1000")
        return next((c["rank"] for c in data["coins"] if c["symbol"] == base), None)
    except Exception:
        return None


_thread = None

def start(every_s: int = 900) -> threading.Thread:
    """Idempotent: bot.py starts agents from auto_start() AND from the Telegram /start command."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return _thread
    def loop():
        while True:
            refresh()
            time.sleep(every_s)
    t = threading.Thread(target=loop, name="Trending", daemon=True)
    t.start()
    _thread = t
    return t


if __name__ == "__main__":
    d = refresh()
    print([(c["rank"], c["symbol"]) for c in d.get("coins", [])] or d)
    print("BTCUSDT rank:", rank("BTCUSDT"))
