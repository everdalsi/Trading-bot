"""
📊 PERFORMANCE TRACKER V6 — Analytics Institutionnels Complets
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AMÉLIORATIONS V6 :
- Sortino Ratio : Sharpe mais uniquement sur volatilité baissière (plus réaliste)
- Calmar Ratio : rendement annualisé / max drawdown (benchmark hedge funds)
- Recovery Factor : profit total / max drawdown (robustesse)
- Trade Duration Analysis : durée moyenne gagnante vs perdante
- Rolling WR : winrate glissant sur 10, 20, 50 trades
- CAGR : taux de croissance annuel composé
- Monthly P&L : distribution mensuelle des résultats
- Streak Analysis V2 : probabilité de run de pertes à venir
"""

import sqlite3
import json
import numpy as np
import os
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime, timedelta
from agents.base_agent import BaseAgent
from logging_config import logger

DB_FILE = "sim_v7.db"


def safe_pnl(pnl_pct: float, amount_usd: float, leverage: float = 1.0) -> float:
    """Calcule le PnL avec protection contre les valeurs aberrantes."""
    max_loss = amount_usd * leverage
    raw = pnl_pct * amount_usd * leverage
    return round(max(-max_loss, min(max_loss * 5, raw)), 4)


class PerformanceTracker(BaseAgent):

    def __init__(self):
        super().__init__(
            name="performance",
            role="Analytics institutionnels : Sortino, Calmar, Sharpe, drawdown, rolling WR, CAGR"
        )
        # BUG FIX (2026-07-24): this used to call _ensure_tables(), which created
        # its own "trades" table (20 columns) racing bot.py's own init_db() (19
        # columns, different order) for the same table in the same DB file.
        # Whichever ran first won, and the loser's INSERTs failed on every single
        # trade close ("table trades has 20 columns but 19 values were supplied"
        # x 8700+ in production logs). log_trade() below -- the only thing that
        # would have written to this table -- is never called anywhere in the
        # codebase, so the table was pure dead weight. bot.py's init_db() is the
        # sole owner of the trades table now.

    def log_trade(self, trade_data: dict):
        try:
            # Calcul durée si time_in / time_out disponibles
            duration_min = None
            if trade_data.get("time_in") and trade_data.get("time_out"):
                try:
                    t1 = datetime.fromisoformat(trade_data["time_in"])
                    t2 = datetime.fromisoformat(trade_data["time_out"])
                    duration_min = (t2 - t1).total_seconds() / 60
                except Exception:
                    pass

            con = sqlite3.connect(DB_FILE)
            con.execute("""
                INSERT OR IGNORE INTO trades
                    (id, symbol, market, side, price_in, price_out, qty, amount_usd,
                     pnl, pnl_pct, result, confidence, reason, time_in, time_out,
                     duration_min, patterns, leverage, kelly_pct)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                trade_data.get("id", -1),
                trade_data.get("symbol", "UNKNOWN"),
                trade_data.get("market", "SPOT"),
                trade_data.get("side", "LONG"),
                trade_data.get("price_in", 0),
                trade_data.get("price_out"),
                trade_data.get("qty", 0),
                trade_data.get("amount_usd", 0),
                trade_data.get("pnl"),
                trade_data.get("pnl_pct"),
                trade_data.get("result"),
                trade_data.get("confidence", 0.5),
                trade_data.get("reason", ""),
                trade_data.get("time_in"),
                trade_data.get("time_out"),
                duration_min,
                json.dumps(trade_data.get("patterns", [])),
                trade_data.get("leverage", 1.0),
                trade_data.get("kelly_pct", 0.0),
            ))
            con.commit()
            con.close()
        except Exception as e:
            logger.error(f"[PERF-TRACKER] log_trade error: {e}")

    def update_trade_results(self, memory: dict, current_price: float) -> dict:
        try:
            sim_data = memory.get("sim", memory)
            trades = sim_data.get("trades", [])
            for trade in trades:
                if trade.get("result") not in ("pending", None) and trade.get("pnl") is not None:
                    continue
                entry = trade.get("price_in") or trade.get("entry_price")
                if not entry or entry <= 0:
                    continue
                side = trade.get("side", "LONG")
                is_buy = side in ("LONG", "BUY")
                pnl_pct = (current_price - entry) / entry if is_buy else (entry - current_price) / entry
                amount = trade.get("amount_usd", 100)
                leverage = trade.get("leverage", 1)
                trade["pnl_pct"] = round(pnl_pct * 100 * leverage, 2)
                trade["pnl"]     = safe_pnl(pnl_pct, amount, leverage)
                trade["result"]  = "win" if pnl_pct > 0 else "loss" if pnl_pct < 0 else "neutral"
            return memory
        except Exception as e:
            logger.error(f"[PERF-TRACKER] update_trade_results error: {e}")
            return memory

    # ────────────────────────────────────────────────────────────────────────
    # CALCULS FINANCIERS AVANCÉS
    # ────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _calc_sharpe(pnl_pcts: List[float], rf: float = 0.0) -> float:
        """Sharpe ratio annualisé (252 trades/an comme proxy)."""
        if len(pnl_pcts) < 5:
            return 0.0
        arr = np.array(pnl_pcts)
        std = np.std(arr, ddof=1)
        if std == 0:
            return 0.0
        return round((np.mean(arr) - rf) / std * np.sqrt(252), 3)

    @staticmethod
    def _calc_sortino(pnl_pcts: List[float], rf: float = 0.0) -> float:
        """
        Sortino ratio — uniquement volatilité négative.
        Meilleur que Sharpe pour les stratégies asymétriques.
        """
        if len(pnl_pcts) < 5:
            return 0.0
        arr = np.array(pnl_pcts)
        downside = arr[arr < rf]
        if len(downside) == 0:
            return 5.0  # aucune perte = Sortino maximal
        downside_std = np.std(downside, ddof=1)
        if downside_std == 0:
            return 0.0
        return round((np.mean(arr) - rf) / downside_std * np.sqrt(252), 3)

    @staticmethod
    def _calc_max_drawdown(closed: List[Dict]) -> float:
        """Max drawdown sur equity curve cumulée."""
        if not closed:
            return 0.0
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in closed:
            equity += t.get("pnl", 0)
            if equity > peak:
                peak = equity
            dd = (equity - peak) / peak if peak > 0 else 0
            if dd < max_dd:
                max_dd = dd
        return max_dd  # négatif

    @staticmethod
    def _calc_calmar(pnl_pcts: List[float], max_dd_pct: float) -> float:
        """
        Calmar ratio = rendement moyen / |max drawdown|.
        Benchmark hedge funds : > 1.0 = bon, > 3.0 = excellent.
        """
        if max_dd_pct == 0 or len(pnl_pcts) < 5:
            return 0.0
        annual_return = np.mean(pnl_pcts) * 252
        return round(annual_return / abs(max_dd_pct), 3)

    @staticmethod
    def _calc_expectancy(closed: List[Dict]) -> float:
        """Espérance mathématique : (WR × avgWin) - (LR × |avgLoss|)."""
        wins   = [t for t in closed if t.get("pnl", 0) > 0]
        losses = [t for t in closed if t.get("pnl", 0) <= 0]
        total  = len(closed)
        if not total:
            return 0.0
        wr     = len(wins) / total
        lr     = 1 - wr
        avg_w  = np.mean([t["pnl"] for t in wins]) if wins else 0
        avg_l  = abs(np.mean([t["pnl"] for t in losses])) if losses else 0
        return round(wr * avg_w - lr * avg_l, 4)

    @staticmethod
    def _calc_recovery_factor(gross_profit: float, max_dd_abs: float) -> float:
        """Recovery factor = profit total / max drawdown absolu."""
        if max_dd_abs == 0:
            return 0.0
        return round(gross_profit / abs(max_dd_abs), 2)

    @staticmethod
    def _rolling_winrate(closed: List[Dict], window: int) -> float:
        """WR sur les N derniers trades."""
        if len(closed) < window:
            return 0.0
        recent = closed[-window:]
        wins = sum(1 for t in recent if t.get("pnl", 0) > 0)
        return round(wins / window * 100, 1)

    @staticmethod
    def _calc_avg_duration(closed: List[Dict]) -> Dict[str, float]:
        """Durée moyenne des trades gagnants vs perdants (si disponible)."""
        wins   = [t for t in closed if t.get("pnl", 0) > 0 and t.get("duration_min")]
        losses = [t for t in closed if t.get("pnl", 0) <= 0 and t.get("duration_min")]
        return {
            "avg_win_min":  round(np.mean([t["duration_min"] for t in wins]), 1)  if wins   else 0.0,
            "avg_loss_min": round(np.mean([t["duration_min"] for t in losses]), 1) if losses else 0.0,
        }

    @staticmethod
    def _get_streak(closed: List[Dict]) -> Tuple[str, int]:
        if not closed:
            return "neutral", 0
        streak_type = "win" if closed[-1].get("pnl", 0) > 0 else "loss"
        count = 0
        for t in reversed(closed):
            current = "win" if t.get("pnl", 0) > 0 else "loss"
            if current == streak_type:
                count += 1
            else:
                break
        return streak_type, count

    @staticmethod
    def _check_degradation(closed: List[Dict]) -> Tuple[bool, str]:
        if len(closed) < 40:
            return False, ""
        recent = [t for t in closed[-20:] if t.get("pnl", 0) > 0]
        prev   = [t for t in closed[-40:-20] if t.get("pnl", 0) > 0]
        wr_r = len(recent) / 20 * 100
        wr_p = len(prev)   / 20 * 100
        if wr_r < wr_p - 15:
            return True, f"WR dégradé : {wr_r:.0f}% vs {wr_p:.0f}% (20 trades précédents)"
        return False, ""

    def _get_lesson_count(self) -> int:
        try:
            con = sqlite3.connect(DB_FILE)
            c = con.execute("SELECT COUNT(*) FROM memory_lessons").fetchone()
            con.close()
            return c[0] if c else 0
        except Exception:
            return 0

    @staticmethod
    def _empty_stats() -> Dict[str, Any]:
        return {
            "total_trades": 0, "wins": 0, "losses": 0,
            "winrate": 0.0, "recent_wr_10": 0.0, "recent_wr_20": 0.0, "recent_wr_50": 0.0,
            "profit_factor": 0.0, "sharpe": 0.0, "sortino": 0.0, "calmar": 0.0,
            "recovery_factor": 0.0, "expectancy": 0.0, "max_drawdown": 0.0,
            "gross_profit": 0.0, "gross_loss": 0.0,
            "streak_type": "neutral", "streak_count": 0,
            "degraded": False, "degradation_msg": "",
            "avg_win": 0.0, "avg_loss": 0.0, "best_trade": 0.0, "worst_trade": 0.0,
            "winrate_goal_met": False
        }

    # ────────────────────────────────────────────────────────────────────────
    # STATS GLOBALES V6
    # ────────────────────────────────────────────────────────────────────────

    def get_global_stats(self, memory: dict) -> Dict[str, Any]:
        try:
            sim_data = memory.get("sim", memory)
            trades   = sim_data.get("trades", [])
            closed   = [t for t in trades if isinstance(t.get("pnl"), (int, float))]
            if not closed:
                return self._empty_stats()

            wins   = [t for t in closed if t["pnl"] > 0]
            losses = [t for t in closed if t["pnl"] <= 0]
            total  = len(closed)
            winrate = round(len(wins) / total * 100, 2)

            gross_profit = sum(t["pnl"] for t in wins)
            gross_loss   = abs(sum(t["pnl"] for t in losses))
            profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0.0

            pnl_pcts = [t.get("pnl_pct", t["pnl"]) for t in closed]
            max_dd   = self._calc_max_drawdown(closed)
            max_dd_abs = abs(sum(t["pnl"] for t in losses)) if losses else 0

            sharpe   = self._calc_sharpe(pnl_pcts)
            sortino  = self._calc_sortino(pnl_pcts)
            calmar   = self._calc_calmar(pnl_pcts, max_dd)
            recovery = self._calc_recovery_factor(gross_profit, max_dd_abs)
            expectancy = self._calc_expectancy(closed)
            duration = self._calc_avg_duration(closed)

            streak_type, streak_count = self._get_streak(closed)
            degraded, degradation_msg = self._check_degradation(closed)

            recent = closed[-20:]
            recent_wr = round(sum(1 for t in recent if t["pnl"] > 0) / len(recent) * 100, 1) if recent else 0.0

            return {
                "total_trades":    total,
                "wins":            len(wins),
                "losses":          len(losses),
                "winrate":         winrate,
                "recent_winrate":  recent_wr,
                "rolling_wr_10":   self._rolling_winrate(closed, 10),
                "rolling_wr_20":   self._rolling_winrate(closed, 20),
                "rolling_wr_50":   self._rolling_winrate(closed, 50),
                "profit_factor":   profit_factor,
                "sharpe":          sharpe,
                "sortino":         sortino,          # NEW V6
                "calmar":          calmar,           # NEW V6
                "recovery_factor": recovery,         # NEW V6
                "gross_profit":    round(gross_profit, 2),
                "gross_loss":      round(gross_loss, 2),
                "expectancy":      round(expectancy, 4),
                "max_drawdown":    round(max_dd * 100, 2),
                "streak_type":     streak_type,
                "streak_count":    streak_count,
                "degraded":        degraded,
                "degradation_msg": degradation_msg,
                "avg_win":         round(np.mean([t["pnl"] for t in wins]), 4)   if wins   else 0,
                "avg_loss":        round(np.mean([t["pnl"] for t in losses]), 4) if losses else 0,
                "avg_win_dur_min": duration["avg_win_min"],   # NEW V6
                "avg_loss_dur_min": duration["avg_loss_min"], # NEW V6
                "best_trade":      round(max(t["pnl"] for t in closed), 4),
                "worst_trade":     round(min(t["pnl"] for t in closed), 4),
                "winrate_goal_met": winrate >= 92.0,
            }
        except Exception as e:
            logger.error(f"[PERF-TRACKER] get_global_stats error: {e}")
            return self._empty_stats()

    def get_symbol_stats(self, memory: dict, symbol: str) -> Dict[str, Any]:
        try:
            sim_data = memory.get("sim", memory)
            trades   = sim_data.get("trades", [])
            closed   = [t for t in trades
                        if isinstance(t.get("pnl"), (int, float)) and t.get("symbol") == symbol]
            if not closed:
                return {"symbol": symbol, "total": 0, "winrate": 0.0, "profit_factor": 0.0,
                        "avg_pnl": 0.0, "expectancy": 0.0, "sortino": 0.0}
            wins = [t for t in closed if t["pnl"] > 0]
            pnl_pcts = [t.get("pnl_pct", t["pnl"]) for t in closed]
            return {
                "symbol":          symbol,
                "total":           len(closed),
                "wins":            len(wins),
                "losses":          len(closed) - len(wins),
                "winrate":         round(len(wins) / len(closed) * 100, 2),
                "profit_factor":   round(sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in closed if t["pnl"] <= 0)), 2) if any(t["pnl"] <= 0 for t in closed) else 0,
                "avg_pnl":         round(np.mean([t["pnl"] for t in closed]), 4),
                "expectancy":      round(self._calc_expectancy(closed), 4),
                "sortino":         self._calc_sortino(pnl_pcts),
                "max_drawdown":    round(self._calc_max_drawdown(closed) * 100, 2),
                "rolling_wr_10":   self._rolling_winrate(closed, 10),
            }
        except Exception as e:
            logger.error(f"[PERF-TRACKER] get_symbol_stats error: {e}")
            return {"symbol": symbol, "total": 0, "winrate": 0.0, "profit_factor": 0.0, "avg_pnl": 0.0, "expectancy": 0.0, "sortino": 0.0}

    def generate_dashboard_data(self, memory: dict) -> Dict[str, Any]:
        stats = self.get_global_stats(memory)
        symbol_stats = {}
        for trade in memory.get("sim", memory).get("trades", []):
            sym = trade.get("symbol")
            if sym and sym not in symbol_stats:
                symbol_stats[sym] = self.get_symbol_stats(memory, sym)

        # Évaluation qualité stratégie basée sur Sortino + Calmar
        strategy_quality = "🔴 À améliorer"
        if stats.get("sortino", 0) > 1.5 and stats.get("calmar", 0) > 1.0:
            strategy_quality = "🟢 Excellente (Sortino>1.5, Calmar>1.0)"
        elif stats.get("sortino", 0) > 0.8:
            strategy_quality = "🟡 Correcte (Sortino>0.8)"

        return {
            "global":          stats,
            "symbols":         symbol_stats,
            "timestamp":       datetime.now().isoformat(),
            "total_lessons":   self._get_lesson_count(),
            "winrate_goal":    "✅ ATTEINT (92%+)" if stats.get("winrate", 0) >= 92 else "🔴 En progression",
            "strategy_quality": strategy_quality,
            "key_metrics": {
                "sharpe":   stats.get("sharpe", 0),
                "sortino":  stats.get("sortino", 0),
                "calmar":   stats.get("calmar", 0),
                "recovery": stats.get("recovery_factor", 0),
            },
            "recommendation": (
                "✅ Passer en live testnet — métriques institutionnelles OK"
                if stats.get("winrate", 0) >= 92 and stats.get("max_drawdown", 0) > -8 and stats.get("sortino", 0) > 1.0
                else "🔄 Continuer simulations + évolution"
            )
        }

    def export_to_json(self, memory: dict, filename: str = "dashboard_performance.json") -> str:
        data = self.generate_dashboard_data(memory)
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)
        return filename

    def validate_winrate_goal(self, memory: dict) -> bool:
        stats = self.get_global_stats(memory)
        goal_met = stats.get("winrate", 0) >= 92.0 and stats.get("max_drawdown", 0) > -8.0
        if goal_met:
            logger.info("🎯 [PERF-TRACKER] OBJECTIF WINRATE 92 %+ ATTEINT !")
        return goal_met

    def export_dashboard(self, memory: dict):
        return self.generate_dashboard_data(memory)

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {"agent": self.name, "summary": "⚠️ Hors domaine performance",
                    "confidence": 0.0, "recommendation": "HOLD"}
        memory = context.get("memory", {})
        stats  = self.get_global_stats(memory)

        quality = "correcte" if stats.get("sortino", 0) > 0.8 else "à améliorer"
        summary = (
            f"📊 Perf V6 — WR: {stats.get('winrate',0):.1f}% ({stats.get('total_trades',0)} trades) | "
            f"Sharpe: {stats.get('sharpe',0):.2f} | Sortino: {stats.get('sortino',0):.2f} | "
            f"Calmar: {stats.get('calmar',0):.2f} | Recovery: {stats.get('recovery_factor',0):.2f} | "
            f"DD: {stats.get('max_drawdown',0):.1f}% | Qualité: {quality}"
        )
        return {
            "agent":        self.name,
            "summary":      summary,
            "global_stats": stats,
            "confidence":   0.98,
            "recommendation": "Stats institutionnelles à jour",
            "glossary_used": True
        }
