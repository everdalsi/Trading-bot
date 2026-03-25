"""
📊 PERFORMANCE TRACKER V3 — Stats temps réel + historique complet
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Améliorations vs V2 :

- Calcul Sharpe ratio en temps réel
- Profit factor par symbole et global
- Streak tracking (série de wins/losses)
- Détection de la dégradation de performance (alerte)
- Intégration avec LearningAgent DB
- Compatible avec sim["trades"] et memory["trades"]
"""

import sqlite3
import json
from typing import Dict, Any, List
from datetime import datetime


DB_FILE = "sim_v7.db"


class PerformanceTracker:

        # ─────────────────────────────────────────────────────────────
    #  MISE À JOUR DES TRADES EN COURS
    # ─────────────────────────────────────────────────────────────
    def update_trade_results(self, memory: dict, current_price: float) -> dict:
        """
        Met à jour les trades 'pending' avec le PnL calculé au prix actuel.
        Compatible avec sim["trades"] et memory["trades"].
        NE RÉASSIGNE PAS memory — modifie en place pour éviter le bug de référence.
        """
        try:
            # Cherche les trades dans sim ou directement dans memory
            sim_data = memory.get("sim", memory)
            trades = sim_data.get("trades", [])

            for trade in trades:
                # Ne traiter que les trades encore ouverts (pending ou sans pnl)
                if trade.get("result") not in ("pending", None) and trade.get("pnl") is not None:
                    continue

                entry = trade.get("price_in") or trade.get("entry_price")
                if not entry or entry <= 0:
                    continue

                side = trade.get("side", "LONG")
                is_buy = side == "LONG" or trade.get("decision") == "BUY"

                if is_buy:
                    pnl_pct = (current_price - entry) / entry
                else:
                    pnl_pct = (entry - current_price) / entry

                amount = trade.get("amount_usd", 100)
                leverage = trade.get("leverage", 1)

                trade["pnl_pct"] = round(pnl_pct * 100 * leverage, 2)

                # === PATCH SAFETY : empêche les +148713% fantômes sur memecoins ===
                trade["pnl"]     = safe_pnl(pnl_pct, amount, leverage)

                trade["result"]  = "win" if pnl_pct > 0 else "loss" if pnl_pct < 0 else "neutral"

            return memory  # retourne la même référence (pas de réassignation)

        except Exception as e:
            print(f"[PERF-TRACKER] update_trade_results error: {e}")
            return memory

    # ─────────────────────────────────────────────────────────────
    #  ENREGISTREMENT D'UN NOUVEAU TRADE
    # ─────────────────────────────────────────────────────────────
    def log_trade(self, trade_data: dict) -> None:
        """Enregistre un trade ouvert (state: pending)."""
        try:
            # Sauvegarde légère en DB pour le tracking
            con = sqlite3.connect(DB_FILE)
            con.execute("""
                INSERT OR IGNORE INTO trades
                    (id, symbol, market, side, price_in, qty, amount_usd,
                     confidence, reason, time_in, patterns, leverage, kelly_pct)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                trade_data.get("id", -1),
                trade_data.get("symbol", "UNKNOWN"),
                trade_data.get("market", "SPOT"),
                trade_data.get("side", "LONG"),
                trade_data.get("price_in", 0),
                trade_data.get("qty", 0),
                trade_data.get("amount_usd", 0),
                trade_data.get("confidence", 0),
                trade_data.get("reason", ""),
                trade_data.get("time_in", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                json.dumps(trade_data.get("patterns", [])),
                trade_data.get("leverage", 1),
                trade_data.get("kelly_pct", 0),
            ))
            con.commit()
            con.close()
        except Exception as e:
            print(f"[PERF-TRACKER] log_trade error: {e}")

    # ─────────────────────────────────────────────────────────────
    #  STATS GLOBALES
    # ─────────────────────────────────────────────────────────────
    def get_global_stats(self, memory: dict) -> Dict[str, Any]:
        """
        Stats globales complètes : WR, Sharpe, profit factor, streak, dégradation.
        """
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

            # Profit factor
            gross_profit = sum(t["pnl"] for t in wins)
            gross_loss   = abs(sum(t["pnl"] for t in losses))
            profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0.0

            # Sharpe ratio (simplifié, basé sur pnl_pct)
            pnl_pcts = [t.get("pnl_pct", t["pnl"]) for t in closed]
            sharpe = self._calc_sharpe(pnl_pcts)

            # Streak actuel
            streak_type, streak_count = self._get_streak(closed)

            # Dégradation de performance (30 derniers vs 30 précédents)
            degraded, degradation_msg = self._check_degradation(closed)

            # Stats récentes (20 derniers)
            recent = closed[-20:]
            recent_wins = sum(1 for t in recent if t["pnl"] > 0)
            recent_wr   = round(recent_wins / len(recent) * 100, 1) if recent else 0.0

            return {
                "total_trades": total,
                "wins": len(wins),
                "losses": len(losses),
                "winrate": winrate,
                "recent_winrate": recent_wr,
                "profit_factor": profit_factor,
                "sharpe": sharpe,
                "gross_profit": round(gross_profit, 2),
                "gross_loss": round(gross_loss, 2),
                "streak_type": streak_type,
                "streak_count": streak_count,
                "degraded": degraded,
                "degradation_msg": degradation_msg,
                "avg_win":  round(sum(t["pnl"] for t in wins) / len(wins), 4) if wins else 0,
                "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 4) if losses else 0,
                "best_trade": round(max(t["pnl"] for t in closed), 4),
                "worst_trade": round(min(t["pnl"] for t in closed), 4),
            }

        except Exception as e:
            print(f"[PERF-TRACKER] get_global_stats error: {e}")
            return self._empty_stats()

    def get_symbol_stats(self, memory: dict, symbol: str) -> Dict[str, Any]:
        """Stats détaillées pour un symbole spécifique."""
        try:
            sim_data = memory.get("sim", memory)
            trades   = sim_data.get("trades", [])
            closed   = [t for t in trades
                        if isinstance(t.get("pnl"), (int, float))
                        and t.get("symbol") == symbol]

            if not closed:
                return {"symbol": symbol, "total": 0, "winrate": 0.0,
                        "profit_factor": 0.0, "avg_pnl": 0.0}

            wins = [t for t in closed if t["pnl"] > 0]
            return {
                "symbol": symbol,
                "total": len(closed),
                "wins": len(wins),
                "losses": len(closed) - len(wins),
                "winrate": round(len(wins) / len(closed) * 100, 1),
                "total_pnl": round(sum(t["pnl"] for t in closed), 4),
                "avg_pnl": round(sum(t["pnl"] for t in closed) / len(closed), 4),
            }
        except Exception as e:
            print(f"[PERF-TRACKER] get_symbol_stats error: {e}")
            return {"symbol": symbol, "total": 0, "winrate": 0.0}

    # ─────────────────────────────────────────────────────────────
    #  MÉTHODES INTERNES
    # ─────────────────────────────────────────────────────────────
    def _calc_sharpe(self, pnl_pcts: list, risk_free: float = 0.0) -> float:
        """Calcule le Sharpe ratio sur une liste de PnL%."""
        try:
            if len(pnl_pcts) < 5:
                return 0.0
            avg = sum(pnl_pcts) / len(pnl_pcts)
            variance = sum((x - avg) ** 2 for x in pnl_pcts) / len(pnl_pcts)
            std = variance ** 0.5
            if std == 0:
                return 0.0
            return round((avg - risk_free) / std * (252 ** 0.5), 2)
        except Exception:
            return 0.0

    def _get_streak(self, closed: list) -> tuple:
        """Retourne (type_streak, longueur) depuis les trades récents."""
        if not closed:
            return ("neutral", 0)
        streak_type = "win" if closed[-1]["pnl"] > 0 else "loss"
        count = 0
        for t in reversed(closed):
            is_win = t["pnl"] > 0
            if (streak_type == "win" and is_win) or (streak_type == "loss" and not is_win):
                count += 1
            else:
                break
        return (streak_type, count)

    def _check_degradation(self, closed: list) -> tuple:
        """Détecte si la performance se dégrade (30 derniers vs 30 précédents)."""
        if len(closed) < 60:
            return (False, "")
        recent = closed[-30:]
        previous = closed[-60:-30]
        wr_recent   = sum(1 for t in recent if t["pnl"] > 0) / 30 * 100
        wr_previous = sum(1 for t in previous if t["pnl"] > 0) / 30 * 100
        diff = wr_recent - wr_previous
        if diff <= -15:
            return (True, f"⚠️ Performance dégradée: WR {wr_recent:.0f}% vs {wr_previous:.0f}% avant")
        return (False, "")

    def _empty_stats(self) -> Dict[str, Any]:
        return {
            "total_trades": 0, "wins": 0, "losses": 0,
            "winrate": 0.0, "recent_winrate": 0.0,
            "profit_factor": 0.0, "sharpe": 0.0,
            "gross_profit": 0.0, "gross_loss": 0.0,
            "streak_type": "neutral", "streak_count": 0,
            "degraded": False, "degradation_msg": "",
            "avg_win": 0.0, "avg_loss": 0.0,
            "best_trade": 0.0, "worst_trade": 0.0,
        }

    def format_stats(self, stats: dict) -> str:
        """Formate les stats pour Telegram."""
        streak_e = "🔥" if stats["streak_type"] == "win" else "❄️"
        degrade_str = f"\n{stats['degradation_msg']}" if stats.get("degraded") else ""
        return (
            f"📊 PERFORMANCE TRACKER\n━━━━━━━━━━━━━\n"
            f"Trades : {stats['total_trades']}\n"
            f"WR     : {stats['winrate']}% (récent: {stats['recent_winrate']}%)\n"
            f"Sharpe : {stats['sharpe']}\n"
            f"P.Factor: {stats['profit_factor']}\n"
            f"Streak : {streak_e} {stats['streak_count']} {stats['streak_type']}(s)\n"
            f"Meilleur: ${stats['best_trade']:+.4f}\n"
            f"Pire   : ${stats['worst_trade']:+.4f}"
            f"{degrade_str}"
        )
