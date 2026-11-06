"""Accountant (comptable): real-time result per trade, per desk and in total (user 06/11: "construis tout").

Every ACCOUNTANT_EVERY_S, from the ledgers themselves (no estimate): for each book, realised P&L (closed trades,
fees included as booked), unrealised P&L at the current price, exposure long/short, fees paid, today's realised
result; then the total. -> DATA_DIR/accounting.json, one Accountant event per pass. A price that cannot be read
leaves that position at cost and is counted in "unpriced" (never a guessed value). Never raises.
"""
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
OUT = DATA_DIR / "accounting.json"
EVERY_S = int(os.environ.get("ACCOUNTANT_EVERY_S", 300))
BOOKS = {"crypto_copy": ("hl_copy_ledger.json", "amount_usd", "price_in", "pnl"),
         "court_terme": ("meme_sim.json", "usd", "entry", "pnl")}


def _json(name: str) -> dict:
    try:
        return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _prices(symbols: list[str]) -> dict:
    try:
        from data_handler import get_prices_batch
        return get_prices_batch(symbols) or {}
    except Exception:
        return {}


def run() -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out, tot = {"at": time.time(), "books": {}}, {"equity": 0.0, "realised": 0.0, "unrealised": 0.0, "today": 0.0}
    for book, (fname, usd_k, entry_k, pnl_k) in BOOKS.items():
        led = _json(fname)
        if not led:
            out["books"][book] = {"available": False}
            continue
        pos = list((led.get("positions") or {}).values())
        prices = _prices(sorted({p.get("symbol") for p in pos if p.get("symbol")}))
        unreal, unpriced, lng, sht = 0.0, 0, 0.0, 0.0
        for p in pos:
            usd, entry, px = float(p.get(usd_k) or 0), float(p.get(entry_k) or 0), prices.get(p.get("symbol"))
            short = p.get("side") == "SHORT"
            sht, lng = (sht + usd, lng) if short else (sht, lng + usd)
            if usd and entry and px:
                unreal += usd * ((px / entry - 1) * (-1 if short else 1))
            else:
                unpriced += 1
        trades = [t for t in (led.get("trades") or []) if t.get(pnl_k) is not None]
        realised = sum(float(t[pnl_k]) for t in trades)
        day = sum(float(t[pnl_k]) for t in trades if str(t.get("time_out") or t.get("closed_at") or "").startswith(today)
                  or (isinstance(t.get("closed"), (int, float)) and datetime.fromtimestamp(t["closed"], timezone.utc).strftime("%Y-%m-%d") == today))
        cash = float(led.get("cash") or 0)
        equity = cash + lng + sht + unreal
        out["books"][book] = {"available": True, "equity": round(equity, 2), "cash": round(cash, 2),
                              "realised": round(realised, 2), "unrealised": round(unreal, 2), "today_realised": round(day, 2),
                              "closed_trades": len(trades), "open_positions": len(pos), "long_usd": round(lng, 2),
                              "short_usd": round(sht, 2), "unpriced_positions": unpriced}
        tot["equity"] += equity
        tot["realised"] += realised
        tot["unrealised"] += unreal
        tot["today"] += day
    out["total"] = {k: round(v, 2) for k, v in tot.items()}
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    return out


_started = False


def start() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        from agent_events import log_event
        time.sleep(90)
        while True:
            try:
                d = run()
                t = d["total"]
                log_event("Accountant", "systeme", "INFO", "",
                          f"capital {t['equity']:.2f} $ : realise {t['realised']:+.2f} $, latent {t['unrealised']:+.2f} $, "
                          f"aujourd'hui {t['today']:+.2f} $", result="cycle")
            except Exception as e:
                try:
                    from agent_events import log_event
                    log_event("Accountant", "systeme", "INFO", "", f"erreur {type(e).__name__}: {str(e)[:120]}")
                except Exception:
                    pass
            time.sleep(EVERY_S)
    threading.Thread(target=loop, daemon=True, name="Accountant").start()
