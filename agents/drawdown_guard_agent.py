"""
🛑 DRAWDOWN GUARD AGENT V1.0 — Circuit Breaker Indépendant
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Rôle : Circuit breaker dédié, entièrement indépendant du RiskAgent.
       3 pertes consécutives  → pause 15 min
       5 pertes consécutives  → pause 1h
       8 pertes consécutives  → arrêt total + alerte Telegram
       Contourne EXTREME_LEARNING_MODE — veto absolu et non négociable.
Priorité : HAUTE — Empêche les spirales de pertes.
"""

import time
import asyncio
import os
from typing import Dict, Any, List, Optional
from datetime import datetime

from agents.base_agent import BaseAgent
from logging_config import logger

# Seuils de pertes consécutives
CONSECUTIVE_3_PAUSE_SEC = 15 * 60   # 15 min
CONSECUTIVE_5_PAUSE_SEC = 60 * 60   # 1h
CONSECUTIVE_8_FULL_STOP = True      # Arrêt total + alerte Telegram

# Drawdown journalier maximum (override EXTREME_LEARNING_MODE)
MAX_DAILY_DD_PCT   = 0.12   # -12% = stop absolu
MAX_OVERALL_DD_PCT = 0.20   # -20% drawdown total = stop absolu


class DrawdownGuardAgent(BaseAgent):
    """
    Circuit breaker indépendant et non contournable.
    Surveille les pertes consécutives et le drawdown,
    indépendamment du mode EXTREME_LEARNING.
    """

    def __init__(self):
        super().__init__(
            name="drawdown_guard",
            role=(
                "Circuit breaker indépendant — 3 pertes: pause 15min, "
                "5 pertes: pause 1h, 8 pertes: arrêt total + alerte Telegram"
            )
        )
        self._consecutive_losses: int = 0
        self._pause_until: Optional[float] = None
        self._full_stop:   bool            = False
        self._full_stop_reason: str        = ""
        self._loss_history: List[dict]     = []
        self._telegram_alert_sent: bool    = False

    # ── Domaine ────────────────────────────────────────────────────────────
    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        return any(kw in q for kw in [
            "drawdown", "circuit breaker", "pertes consécutives",
            "consecutive", "guard", "drawdown_guard", "loss streak",
            "stop", "pause", "risque",
        ]) or super()._is_in_my_domain(question)

    # ── Mise à jour depuis bot.py ───────────────────────────────────────────
    def record_trade_result(self, won: bool, pnl_usd: float = 0.0, symbol: str = "") -> dict:
        """
        Appelé par bot.py après chaque trade clôturé.
        Retourne un dict avec l'action à prendre (pause, stop, ok).
        """
        now = time.time()

        if won:
            # Reset streak sur victoire
            self._consecutive_losses = 0
            self._telegram_alert_sent = False
            self._loss_history = []
            return {"action": "ok", "consecutive_losses": 0}

        # Perte → incrémenter
        self._consecutive_losses += 1
        self._loss_history.append({
            "ts":      now,
            "symbol":  symbol,
            "pnl_usd": pnl_usd,
        })

        result = {
            "consecutive_losses": self._consecutive_losses,
            "action":             "ok",
            "pause_minutes":      0,
            "telegram_alert":     False,
        }

        if self._consecutive_losses >= 8:
            self._full_stop       = True
            self._full_stop_reason = f"{self._consecutive_losses} pertes consécutives"
            result["action"]     = "full_stop"
            result["telegram_alert"] = True
            logger.critical(
                f"[DRAWDOWN_GUARD] 🚨 ARRÊT TOTAL — {self._consecutive_losses} pertes consécutives"
            )

        elif self._consecutive_losses >= 5:
            self._pause_until     = now + CONSECUTIVE_5_PAUSE_SEC
            result["action"]      = "pause"
            result["pause_minutes"] = CONSECUTIVE_5_PAUSE_SEC // 60
            logger.warning(
                f"[DRAWDOWN_GUARD] ⏸️ PAUSE 1H — {self._consecutive_losses} pertes consécutives"
            )

        elif self._consecutive_losses >= 3:
            self._pause_until     = now + CONSECUTIVE_3_PAUSE_SEC
            result["action"]      = "pause"
            result["pause_minutes"] = CONSECUTIVE_3_PAUSE_SEC // 60
            logger.warning(
                f"[DRAWDOWN_GUARD] ⏸️ PAUSE 15min — {self._consecutive_losses} pertes consécutives"
            )

        return result

    def check_drawdown(self, equity: float, peak_equity: float, daily_start: float) -> dict:
        """Vérifie le drawdown global et journalier."""
        overall_dd = (equity - peak_equity) / peak_equity if peak_equity > 0 else 0
        daily_dd   = (equity - daily_start) / daily_start  if daily_start > 0 else 0

        if overall_dd <= -MAX_OVERALL_DD_PCT:
            self._full_stop = True
            self._full_stop_reason = f"Drawdown total {overall_dd*100:.1f}%"
            return {"action": "full_stop", "reason": self._full_stop_reason}

        if daily_dd <= -MAX_DAILY_DD_PCT:
            self._full_stop = True
            self._full_stop_reason = f"Drawdown journalier {daily_dd*100:.1f}%"
            return {"action": "full_stop", "reason": self._full_stop_reason}

        return {"action": "ok", "overall_dd": overall_dd, "daily_dd": daily_dd}

    def reset_full_stop(self) -> None:
        """Reset manuel (via commande Telegram /reset_stop)."""
        self._full_stop             = False
        self._full_stop_reason      = ""
        self._consecutive_losses    = 0
        self._pause_until           = None
        self._telegram_alert_sent   = False
        logger.info("[DRAWDOWN_GUARD] ✅ Circuit breaker reset manuellement")

    # ── État interne ────────────────────────────────────────────────────────
    def _is_paused(self) -> bool:
        if self._full_stop:
            return True
        if self._pause_until and time.time() < self._pause_until:
            return True
        return False

    def _remaining_pause_min(self) -> int:
        if self._full_stop:
            return -1   # -1 = arrêt permanent
        if self._pause_until:
            return max(0, int((self._pause_until - time.time()) // 60))
        return 0

    # ── Respond ─────────────────────────────────────────────────────────────
    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        # Mise à jour depuis contexte
        streak_type  = context.get("streak_type", "neutral")
        streak_count = context.get("streak_count", 0)
        equity       = context.get("equity", 0)
        peak_equity  = context.get("peak_equity", equity)
        daily_start  = context.get("daily_start_equity", equity)

        # Sync des pertes consécutives depuis le contexte
        if streak_type == "loss" and streak_count > self._consecutive_losses:
            self._consecutive_losses = streak_count

        # Vérif drawdown
        if equity > 0:
            dd_check = self.check_drawdown(equity, peak_equity, daily_start)
            if dd_check["action"] == "full_stop":
                return {
                    "agent":          self.name,
                    "summary":        f"🚨 ARRÊT TOTAL — {dd_check['reason']}",
                    "arguments":      [dd_check["reason"]],
                    "risks":          ["Drawdown critique — capital en danger"],
                    "confidence":     1.0,
                    "recommendation": "NO TRADE — Circuit breaker: drawdown critique",
                    "veto":           True,
                    "veto_reason":    "drawdown_guard_drawdown",
                    "full_stop":      True,
                    "telegram_alert": True,
                }

        paused   = self._is_paused()
        rem_min  = self._remaining_pause_min()
        losses   = self._consecutive_losses

        if self._full_stop:
            return {
                "agent":          self.name,
                "summary":        f"🚨 BOT ARRÊTÉ — {self._full_stop_reason} | {losses} pertes consécutives",
                "arguments":      [f"Circuit breaker activé: {self._full_stop_reason}"],
                "risks":          ["Spirale de pertes — arrêt obligatoire"],
                "confidence":     1.0,
                "recommendation": "BOT ARRÊTÉ — Reset manuel requis (/reset_stop)",
                "veto":           True,
                "veto_reason":    "drawdown_guard_full_stop",
                "full_stop":      True,
                "telegram_alert": not self._telegram_alert_sent,
                "consecutive_losses": losses,
            }

        if paused and losses >= 5:
            return {
                "agent":          self.name,
                "summary":        f"⏸️ PAUSE 1H — {losses} pertes consécutives | {rem_min}min restantes",
                "arguments":      [f"{losses} pertes d'affilée — pause 1h obligatoire"],
                "risks":          ["Streak de pertes — algo possiblement hors marché"],
                "confidence":     1.0,
                "recommendation": f"NO TRADE — Pause {rem_min}min ({losses} pertes consécutives)",
                "veto":           True,
                "veto_reason":    "drawdown_guard_pause_1h",
                "pause_minutes":  rem_min,
                "consecutive_losses": losses,
            }

        if paused and losses >= 3:
            return {
                "agent":          self.name,
                "summary":        f"⏸️ PAUSE 15min — {losses} pertes consécutives | {rem_min}min restantes",
                "arguments":      [f"{losses} pertes d'affilée — pause 15min"],
                "risks":          ["Streak de pertes"],
                "confidence":     1.0,
                "recommendation": f"NO TRADE — Pause {rem_min}min ({losses} pertes consécutives)",
                "veto":           True,
                "veto_reason":    "drawdown_guard_pause_15min",
                "pause_minutes":  rem_min,
                "consecutive_losses": losses,
            }

        # Tout va bien
        return {
            "agent":          self.name,
            "summary":        (
                f"✅ Circuit Breaker OK — "
                f"{losses} perte{'s' if losses != 1 else ''} consécutive{'s' if losses != 1 else ''}"
            ),
            "arguments":      [f"Streak de pertes: {losses}/8 (seuil critique)"],
            "risks":          [],
            "confidence":     0.9,
            "recommendation": "TRADE AUTORISÉ — Circuit breaker nominal",
            "veto":           False,
            "consecutive_losses": losses,
        }

    # ── API publique ────────────────────────────────────────────────────────
    def is_trading_blocked(self) -> bool:
        """Appelable directement depuis bot.py."""
        return self._is_paused()

    def get_status(self) -> dict:
        return {
            "full_stop":          self._full_stop,
            "full_stop_reason":   self._full_stop_reason,
            "paused":             self._is_paused(),
            "pause_minutes":      self._remaining_pause_min(),
            "consecutive_losses": self._consecutive_losses,
        }
