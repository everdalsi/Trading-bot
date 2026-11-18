"""L1-L3: read-only mirrors of the three live paper portfolios, so they sit in the same daily ranking.

Nothing here writes to or imports from bot.py. Each mirror turns one ledger file of DATA_DIR into
{available, equity, pnl_closed, n_trades, win_rate, halted} with the same tolerant reading as dashboard/server.py
(_portfolio): open positions are counted at cost, because the lab does not own their live prices.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

MIRRORS = {
    "L1": ("Copie Hyperliquid (existant)", os.environ.get("LAB_L1_FILE", "hl_copy_ledger.json"), "ledger"),
    "L2": ("Court terme (existant)", os.environ.get("LAB_L2_FILE", "meme_sim.json"), "ledger"),
    "L3": ("MemeHunter (existant, mesure)", os.environ.get("LAB_L3_FILE", "meme_hunter.json"), "meme"),
}


def _num(*values, default: float = 0.0) -> float:
    for v in values:
        try:
            if v is not None:
                return float(v)
        except (TypeError, ValueError):
            continue
    return default


def _pnl(t: dict) -> float:
    return _num(t.get("pnl"), t.get("pnl_usd"))


def summarize_ledger(d: dict) -> dict:
    positions = d.get("positions") or d.get("open_positions") or {}
    if isinstance(positions, dict):
        positions = list(positions.values())
    trades = [t for t in (d.get("trades") or d.get("closed_trades") or [])
              if isinstance(t, dict) and (t.get("pnl") is not None or t.get("pnl_usd") is not None)]
    cost = sum(_num(p.get("amount_usd"), p.get("usd"), p.get("size_usd"), p.get("cost"), p.get("value"))
               for p in positions if isinstance(p, dict))
    cash = _num(d.get("cash"), d.get("virtual_cash"))
    wins = sum(1 for t in trades if _pnl(t) > 0)
    fee_keys = ("fee", "fees", "fee_usd")
    has_fees = any(k in t for t in trades for k in fee_keys)  # unknown, not 0, when the ledger does not record them
    return {"available": True, "equity": round(cash + cost, 2), "pnl_closed": round(sum(map(_pnl, trades)), 2),
            "n_trades": len(trades), "win_rate": round(100 * wins / len(trades), 1) if trades else None,
            "fees": round(sum(_num(*(t.get(k) for k in fee_keys)) for t in trades), 2) if has_fees else None,
            "halted": str(d.get("halted") or d.get("circuit_breaker") or "")}


def summarize_meme(d: dict) -> dict:
    """MemeHunter is a measurement counter of $10 shadow trades, not a dollar portfolio."""
    return {"available": True, "equity": None, "pnl_closed": round(_num(d.get("shadow_net")), 2),
            "n_trades": int(_num(d.get("shadow_closed"))), "win_rate": None, "fees": None,
            "halted": "", "measure_only": True, "candidates": int(_num(d.get("candidates")))}


def read_mirror(slot: str, data_dir: Path) -> dict:
    label, fname, kind = MIRRORS[slot]
    path = data_dir / fname
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False, "label": label, "file": fname}
    out = summarize_meme(d) if kind == "meme" else summarize_ledger(d)
    return {**out, "label": label, "file": fname}
