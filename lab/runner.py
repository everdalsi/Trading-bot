"""Lab process: `python -m lab.runner` (own container/process, never inside bot.py).

Every LAB_STEP_S seconds: BTC/ETH spot prices -> each slot's step (a slot may read more public data through
ctx["data"] and publishes the prices of its own instruments in `slot.marks`) -> mark and snapshot -> mirrors ->
scoreboard.json. Once a day after LAB_REPORT_HOUR_UTC: a short Telegram ranking. Each slot runs in its own
try block, so one failing slot (or one unwritable file) never stops the others; nothing here touches bot.py.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from lab import data as market_data
from lab import mirrors, scoreboard
from lab.ledger import LabLedger
from lab.slots import all_slots

STEP_S = int(os.environ.get("LAB_STEP_S", "300"))
REPORT_HOUR_UTC = int(os.environ.get("LAB_REPORT_HOUR_UTC", "8"))
SYMBOLS = ("BTCUSDT", "ETHUSDT")


def _log(action: str, slot: str, reason: str, **extra) -> None:
    try:
        from agent_events import log_event
        log_event(f"Lab:{slot}", "labo", action, reason=reason, **extra)
    except Exception:  # the journal must never stop the lab
        pass


def fetch_prices(symbols=SYMBOLS) -> dict | None:
    prices = market_data.spot_prices(symbols)
    if all(s in prices for s in symbols):
        return prices
    _log("INFO", "prix", "prix BTC/ETH indisponibles, tour saute")
    return None


def send_telegram(text: str) -> bool:
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=json.dumps({"chat_id": chat, "text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15):
            return True
    except Exception as e:
        _log("INFO", "telegram", f"envoi impossible: {type(e).__name__}")
        return False


def _write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class Lab:
    def __init__(self, data_dir: Path, fetch=fetch_prices, send=send_telegram, now=None, data=market_data,
                 slots=None):
        self.data_dir, self.dir = data_dir, data_dir / "lab"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.fetch, self.send, self.data = fetch, send, data
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.slots = slots if slots is not None else all_slots()
        self.ledgers = {s.name: self._load(s.name) for s in self.slots}
        self.state = self._load_state()

    def _load(self, name: str) -> LabLedger:
        path = self.dir / f"{name}.json"
        try:
            return LabLedger.load(str(path))
        except FileNotFoundError:
            return LabLedger(name=name, initial_cash=1000.0)
        except (OSError, ValueError, KeyError) as e:  # unreadable: keep it aside, never silently overwrite it
            path.replace(path.with_suffix(".bad.json"))
            _log("INFO", name, f"registre illisible mis de cote: {e}")
            return LabLedger(name=name, initial_cash=1000.0)

    def _load_state(self) -> dict:
        path = self.dir / "state.json"
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            state = {}
        except (OSError, ValueError):
            path.replace(path.with_suffix(".bad.json"))
            state = {}
        state.setdefault("mirrors", {})
        return state

    def step(self) -> dict | None:
        prices = self.fetch(SYMBOLS)
        if not prices:
            return None
        now = self.now()
        day, ctx = now.strftime("%Y-%m-%d"), {"data": self.data, "now": now}
        for slot in self.slots:
            ledger = self.ledgers[slot.name]
            try:
                for action in slot.step(ledger, prices, ctx):
                    _log("TRADE", slot.name, action)
                # last known price per open instrument: a source down for a while must not value a leg at cost
                held = {p["symbol"] for p in ledger.positions.values()}
                marks = {**ledger.state.get("_marks", {}), **prices, **getattr(slot, "marks", {})}
                ledger.state["_marks"] = {s: v for s, v in marks.items() if s in held and v}
                ledger.snapshot(day, ledger.mark(marks))
                ledger.save(str(self.dir / f"{slot.name}.json"))
            except Exception as e:
                _log("INFO", slot.name, f"erreur du slot: {type(e).__name__}: {e}")
        mirror_data = {}
        for slot in mirrors.MIRRORS:
            m = mirrors.read_mirror(slot, self.data_dir)
            mirror_data[slot] = m
            snaps = self.state["mirrors"].setdefault(slot, [])
            if m.get("available") and m.get("equity") is not None:
                if snaps and snaps[-1][0] == day:
                    snaps[-1][1] = m["equity"]
                else:
                    snaps.append([day, m["equity"]])
        board = self.scoreboard(mirror_data, day)
        _write_json(self.dir / "scoreboard.json", board)
        if now.hour >= REPORT_HOUR_UTC and self.state.get("last_report_day") != day:
            if self.send(scoreboard.telegram_text(board["rows"], day)):
                self.state["last_report_day"] = day
                _write_json(self.dir / "state.json", self.state)  # at once, so a crash cannot resend it
        _write_json(self.dir / "state.json", self.state)
        return board

    def scoreboard(self, mirror_data: dict, day: str) -> dict:
        bench = self.ledgers[scoreboard.BENCHMARK].curve() if scoreboard.BENCHMARK in self.ledgers else []
        rows = []
        for slot, m in mirror_data.items():
            # mirrors are valued at cost and their fees are not all known: never comparable enough to be "candidat"
            row = scoreboard.build_row(
                slot, m["label"], self.state["mirrors"].get(slot, []), bench, trades=m.get("n_trades", 0),
                win_rate=m.get("win_rate"), fees=m.get("fees"), halted=m.get("halted", ""), measure_only=True)
            if m.get("agents"):
                row["agents"] = m["agents"]  # per-agent results inside the shared portfolio
            rows.append(row)
        for slot in self.slots:
            led = self.ledgers[slot.name]
            st = led.stats()
            row = scoreboard.build_row(slot.name, slot.label, led.curve(), bench, trades=st["total"],
                                       win_rate=st["win_rate"], fees=round(led.fees_paid, 2), halted=led.halted)
            row["open_positions"] = len(led.positions)
            if led.funding_net:
                row["funding_net"] = round(led.funding_net, 2)
            rows.append(row)
        return {"day": day, "updated": self.now().isoformat(timespec="seconds"), "paper_only": True, "rows": rows}


def main() -> None:
    lab = Lab(Path(os.environ.get("DATA_DIR") or "."))
    _log("INFO", "labo", f"demarrage du labo (argent fictif, {len(lab.slots)} slots + 3 miroirs)")
    while True:
        try:
            lab.step()
        except Exception as e:
            _log("INFO", "labo", f"tour en erreur: {type(e).__name__}: {e}")
        time.sleep(STEP_S)


if __name__ == "__main__":
    main()
