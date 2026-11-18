"""Daily ranking of the lab slots and the "candidate for real money" check (spec section 5).

Passing the check never triggers anything: the slot only shows status "candidat"; the user decides.
"""
from __future__ import annotations

from datetime import date

BENCHMARK = "L10"
LOW_ACTIVITY = {"L5", "L8", "L9"}   # judged on 60 days instead of 30 days + 100 trades
MIN_DAYS, MIN_DAYS_LOW, MIN_TRADES, MAX_DD = 30, 60, 100, -15.0


def max_drawdown_pct(snaps: list) -> float:
    peak, worst = None, 0.0
    for _, eq in snaps:
        peak = eq if peak is None else max(peak, eq)
        if peak:
            worst = min(worst, (eq - peak) / peak * 100)
    return round(worst, 2)


def _ret(a: float, b: float) -> float:
    return round((b - a) / a * 100, 2) if a else 0.0


def benchmark_return(bench_snaps: list, start_day: str) -> float | None:
    window = [eq for day, eq in bench_snaps if day >= start_day]
    return _ret(window[0], window[-1]) if len(window) >= 2 else None


def _day(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def calendar_days(snaps: list) -> int:
    """Calendar days covered, gaps included (runner stopped, prices missing)."""
    if not snaps:
        return 0
    a, b = _day(snaps[0][0]), _day(snaps[-1][0])
    return (b - a).days + 1 if a and b else len(snaps)


def halves_positive(snaps: list) -> bool:
    """Both halves of the period (split by DATE, not by number of points) must gain money."""
    if len(snaps) < 4:
        return False
    a, b = _day(snaps[0][0]), _day(snaps[-1][0])
    mid_day = (a + (b - a) / 2).isoformat() if a and b else snaps[len(snaps) // 2][0]
    first = [s for s in snaps if s[0] <= mid_day]
    second = [s for s in snaps if s[0] >= first[-1][0]]
    return len(second) >= 2 and _ret(first[0][1], first[-1][1]) > 0 and _ret(second[0][1], second[-1][1]) > 0


def candidate_checks(slot: str, row: dict, snaps: list) -> dict:
    low = slot in LOW_ACTIVITY
    checks = {
        "duree": row["days"] >= (MIN_DAYS_LOW if low else MIN_DAYS) and (low or row["trades"] >= MIN_TRADES),
        "net_positif": row["return_pct"] > 0,
        "bat_le_temoin": row["vs_benchmark_pts"] is not None and row["vs_benchmark_pts"] > 0,
        "perte_max": row["max_dd_pct"] >= MAX_DD,
        "deux_moities": halves_positive(snaps),
    }
    return checks


def build_row(slot: str, label: str, snaps: list, bench_snaps: list, *, trades: int, win_rate,
              fees, halted: str = "", measure_only: bool = False) -> dict:
    start_eq, last_eq = (snaps[0][1], snaps[-1][1]) if snaps else (None, None)
    ret = _ret(start_eq, last_eq) if snaps else 0.0
    bench = benchmark_return(bench_snaps, snaps[0][0]) if snaps else None
    row = {"slot": slot, "label": label, "equity": last_eq, "start_equity": start_eq, "return_pct": ret,
           "vs_benchmark_pts": None if bench is None or slot == BENCHMARK else round(ret - bench, 2),
           "fees": fees, "trades": trades, "win_rate": win_rate, "max_dd_pct": max_drawdown_pct(snaps),
           "days": calendar_days(snaps), "halted": halted}
    if slot == BENCHMARK:
        row["status"] = "temoin"
    elif measure_only:
        row["status"] = "mesure"
    elif halted:
        row["status"] = "arrete"
    else:
        checks = candidate_checks(slot, row, snaps)
        row["checks"] = checks
        row["status"] = "candidat" if all(checks.values()) else "mesure"
    return row


def telegram_text(rows: list, day: str) -> str:
    lines = [f"LABO (argent fictif) {day}"]
    ranked = sorted((r for r in rows if r["equity"] is not None), key=lambda r: r["return_pct"], reverse=True)
    for r in ranked + [r for r in rows if r["equity"] is None]:
        vs = "" if r["vs_benchmark_pts"] is None else f" | vs temoin {r['vs_benchmark_pts']:+.2f} pts"
        eq = "mesure seule" if r["equity"] is None else f"{r['equity']:.2f}$ ({r['return_pct']:+.2f}%)"
        lines.append(f"{r['slot']} {r['label']} : {eq}{vs} | {r['trades']} trades | {r['status']}")
    lines.append("Candidat = critères remplis ; aucun argent réel sans ta décision.")
    return "\n".join(lines)
