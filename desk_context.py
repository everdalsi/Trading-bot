"""DeskContext: runs Desktop's desk-spec research modules every 10 min (measurement only, no weight yet).

- correlation_arb.concentration(open positions) -> effective independent bets + net BTC correlation of the book;
  correlation_arb.spreads(top coins) -> cross-exchange spreads net of fees;
- onchain_social.whale_flows() (Whale Alert public channel: exchange in/outflows, stablecoin mints),
  listings() (Binance new listings), mentions(held coins) (public Telegram channels).
Writes DATA_DIR/desk_context.json. manipulation.check(symbol) is called inline at entry time (market_tags).
Never raises.
"""
import json
import os
import threading
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "desk_context.json"


def run(positions_fn=None) -> dict:
    out: dict = {"at": time.time(), "mode": "mesure (aucun poids)"}
    positions = []
    try:
        positions = positions_fn() if positions_fn else []
    except Exception:
        pass
    coins = sorted({p["symbol"].replace("USDT", "") for p in positions if p.get("symbol")})[:20]
    for key, fn in (("concentration", lambda: __import__("correlation_arb").concentration(positions)),
                    ("spreads", lambda: __import__("correlation_arb").spreads(coins or ["BTC", "ETH", "SOL"])),
                    ("whale_flows", lambda: __import__("onchain_social").whale_flows()),
                    ("listings", lambda: __import__("onchain_social").listings()),
                    ("mentions", lambda: __import__("onchain_social").mentions(coins or ["BTC", "ETH", "SOL"]))):
        try:
            out[key] = fn()
        except Exception as e:
            out[f"{key}_error"] = type(e).__name__
    try:
        tmp = OUT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(OUT)
    except Exception:
        pass
    return out


_started = False


def start(positions_fn=None, every_s: int = 600) -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(60)
        while True:
            try:
                run(positions_fn)
            except Exception:
                pass
            time.sleep(every_s)
    threading.Thread(target=loop, daemon=True, name="DeskContext").start()
