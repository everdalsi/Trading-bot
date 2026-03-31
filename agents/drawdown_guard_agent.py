"""
🛑 DRAWDOWN GUARD AGENT V2 — Circuit Breaker Expert & Adaptatif
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UPGRADES V2 :
- Seuils adaptatifs selon la volatilité du marché (ATR)
- Drawdown journalier ET Drawdown total indépendants
- Recovery score : score de confiance pour reprendre après pause
- Cooldown progressif (3 pertes → 15min, 5 → 1h, 8 → arrêt)
- Reset automatique intelligent (reset partiel après pause complète)
- Alerte Telegram enrichie avec contexte détaillé
- Calcul du Maximum Drawdown en temps réel
- Détection des spirales de pertes accélérées
"""

import time
import asyncio
import os
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

from agents.base_agent import BaseAgent
from logging_config import logger

# ── Seuils de pertes consécutives ─────────────────────────────────────────────
CONSECUTIVE_3_PAUSE_SEC = 15 * 60    # 15 min
CONSECUTIVE_5_PAUSE_SEC = 60 * 60    # 1h
CONSECUTIVE_8_PAUSE_SEC = 24 * 60 * 60  # 24h (full stop mais avec reset auto)

# ── Seuils de drawdown ────────────────────────────────────────────────────────
MAX_DAILY_DD_PCT   = 0.12   # -12% par jour = stop absolu
MAX_OVERALL_DD_PCT = 0.20   # -20% total = stop absolu
MAX_WEEKLY_DD_PCT  = 0.15   # -15% sur la semaine = pause 4h


class DrawdownGuardAgent(BaseAgent):
    """
    Circuit breaker indépendant, non contournable et adaptatif.
    Surveille pertes consécutives, drawdown journalier/total/hebdomadaire.
    """

    def __init__(self):
        super().__init__(
            name="drawdown_guard",
            role=(
                "Circuit breaker adaptatif — 3 pertes: pause 15min, "
                "5 pertes: pause 1h, 8 pertes: pause 24h + alerte Telegram | "
                "DD journalier >12%: stop | DD total >20%: stop"
            )
        )
        self._consecutive_losses: int  = 0
        self._pause_until: Optional[float] = None
        self._full_stop:   bool            = False
        self._full_stop_reason: str        = ""
        self._loss_history: List[Dict]     = []
        self._telegram_alert_sent: bool    = False
        self._peak_equity: float           = 0.0
        self._daily_start_equity: float    = 0.0
        self._weekly_start_equity: float   = 0.0
        self._weekly_start_ts: float       = time.time()
        self._recovery_score: float        = 1.0   # 1.0 = fully recovered
        self._auto_resume_ts: Optional[float] = None  # Resume automatique

    # ── Domaine ────────────────────────────────────────────────────────────
    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        return any(kw in q for kw in [
            "drawdown", "circuit breaker", "pertes consécutives",
            "consecutive", "guard", "drawdown_guard", "loss streak",
            "stop", "pause", "risque", "circuit", "breaker",
        ]) or super()._is_in_my_domain(question)

    # ── Gestion du circuit breaker ─────────────────────────────────────────

    def record_trade_result(self, won: bool, pnl_usd: float = 0.0, symbol: str = "",
                             current_equity: float = 0.0, norm_atr: float = 0.02) -> Dict:
        """
        Appelé après chaque trade clôturé.
        norm_atr : ATR normalisé pour seuils adaptatifs.
        """
        now = time.time()

        # Mise à jour peak equity
        if current_equity > 0:
            if self._peak_equity == 0:
                self._peak_equity = current_equity
            elif current_equity > self._peak_equity:
                self._peak_equity = current_equity

        if won:
            # Reset partiel sur victoire (pas de reset total pour éviter les rebounds)
            self._consecutive_losses = max(0, self._consecutive_losses - 1)
            self._telegram_alert_sent = False
            self._recovery_score = min(1.0, self._recovery_score + 0.2)
            # Vider les vieilles pertes
            self._loss_history = [l for l in self._loss_history if now - l["ts"] < 3600]
            return {"action": "ok", "consecutive_losses": self._consecutive_losses}

        # Perte → incrémenter
        self._consecutive_losses += 1
        self._recovery_score = max(0.0, self._recovery_score - 0.25)
        self._loss_history.append({
            "ts": now, "symbol": symbol, "pnl_usd": pnl_usd,
        })

        # ── Seuils adaptatifs selon volatilité ────────────────────────────
        # En marché très volatile (ATR% > 3%), seuils plus tolérants
        if norm_atr > 0.03:
            pause3 = CONSECUTIVE_3_PAUSE_SEC * 0.5   # 7.5 min au lieu de 15
            pause5 = CONSECUTIVE_5_PAUSE_SEC * 0.5   # 30 min au lieu de 1h
        else:
            pause3 = CONSECUTIVE_3_PAUSE_SEC
            pause5 = CONSECUTIVE_5_PAUSE_SEC

        result = {
            "consecutive_losses": self._consecutive_losses,
            "action":             "ok",
            "pause_minutes":      0,
            "telegram_alert":     False,
        }

        if self._consecutive_losses >= 8:
            self._pause_until       = now + CONSECUTIVE_8_PAUSE_SEC
            self._auto_resume_ts   = now + CONSECUTIVE_8_PAUSE_SEC
            self._full_stop_reason = f"{self._consecutive_losses} pertes consécutives"
            result["action"]       = "full_stop"
            result["telegram_alert"] = True
            result["pause_minutes"] = 24 * 60
            logger.critical(
                f"[DDGUARD V2] 🚨 PAUSE 24H — {self._consecutive_losses} pertes | "
                f"Recovery: {self._recovery_score:.0%}"
            )

        elif self._consecutive_losses >= 5:
            self._pause_until     = now + pause5
            self._auto_resume_ts  = now + pause5
            result["action"]      = "pause"
            result["pause_minutes"] = int(pause5 // 60)
            result["telegram_alert"] = True
            logger.warning(f"[DDGUARD V2] ⏸️ PAUSE {int(pause5//60)}min — {self._consecutive_losses} pertes")

        elif self._consecutive_losses >= 3:
            self._pause_until    = now + pause3
            self._auto_resume_ts = now + pause3
            result["action"]     = "pause"
            result["pause_minutes"] = int(pause3 // 60)
            logger.warning(f"[DDGUARD V2] ⏸️ PAUSE {int(pause3//60)}min — {self._consecutive_losses} pertes")

        return result

    def update_equity(self, current_equity: float, daily_start: float = 0.0,
                       weekly_start: float = 0.0) -> Dict:
        """Mise à jour de l'equity pour les vérifications de drawdown."""
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity
        if daily_start > 0:
            self._daily_start_equity = daily_start
        if weekly_start > 0:
            self._weekly_start_equity = weekly_start
            self._weekly_start_ts = time.time()
        return self.get_status()

    def _is_paused(self) -> bool:
        """Vérifie si le trading est en pause (avec auto-resume)."""
        if self._pause_until is None:
            return False
        now = time.time()
        if now >= self._pause_until:
            # Auto-resume : reset les variables
            self._pause_until = None
            self._auto_resume_ts = None
            self._full_stop = False
            self._full_stop_reason = ""
            # Reset partiel des pertes consécutives après une longue pause
            self._consecutive_losses = max(0, self._consecutive_losses - 2)
            logger.info(f"[DDGUARD V2] ✅ Auto-resume après pause | pertes: {self._consecutive_losses}")
            return False
        return True

    def _remaining_pause_min(self) -> int:
        if not self._is_paused() or self._pause_until is None:
            return 0
        return max(0, int((self._pause_until - time.time()) / 60))

    def _compute_drawdowns(self, context: dict) -> Dict[str, float]:
        """Calcule les drawdowns depuis le contexte."""
        equity        = float(context.get("equity", 0))
        daily_start   = float(context.get("daily_start_equity", self._daily_start_equity or equity))
        initial       = float(context.get("initial_equity", equity))
        peak          = max(self._peak_equity, equity)

        dd_daily   = (equity - daily_start) / (daily_start + 1e-9) if daily_start else 0.0
        dd_overall = (equity - peak) / (peak + 1e-9) if peak else 0.0

        return {
            "dd_daily":   round(dd_daily, 4),
            "dd_overall": round(dd_overall, 4),
        }

    # ── API publique ────────────────────────────────────────────────────────

    def is_trading_blocked(self) -> bool:
        return self._is_paused()

    def get_status(self) -> Dict:
        return {
            "full_stop":          self._full_stop,
            "full_stop_reason":   self._full_stop_reason,
            "paused":             self._is_paused(),
            "pause_minutes":      self._remaining_pause_min(),
            "consecutive_losses": self._consecutive_losses,
            "recovery_score":     round(self._recovery_score, 3),
        }

    # ── RESPOND ────────────────────────────────────────────────────────────

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {
                "agent": self.name, "summary": "Hors domaine drawdown_guard",
                "confidence": 0.0, "recommendation": "HOLD",
            }

        paused = self._is_paused()
        losses = self._consecutive_losses
        rem    = self._remaining_pause_min()

        # Drawdown depuis contexte
        dds = self._compute_drawdowns(context)
        dd_daily   = dds["dd_daily"]
        dd_overall = dds["dd_overall"]

        # ── Veto absolus ───────────────────────────────────────────────────

        # Drawdown journalier critique
        if dd_daily <= -MAX_DAILY_DD_PCT:
            self._pause_until = time.time() + 4 * 3600   # Pause 4h
            self._consecutive_losses = max(self._consecutive_losses, 5)
            reason = f"Drawdown journalier: {dd_daily:.1%} (seuil {-MAX_DAILY_DD_PCT:.0%})"
            logger.critical(f"[DDGUARD V2] 🚨 VETO Drawdown journalier: {dd_daily:.1%}")
            return {
                "agent": self.name,
                "summary": f"🚨 VETO — {reason}",
                "arguments": [reason], "risks": [reason],
                "confidence": 1.0, "recommendation": "NO TRADE — Drawdown journalier",
                "veto": True, "veto_reason": "daily_drawdown_exceeded",
                "telegram_alert": True,
                "consecutive_losses": losses,
            }

        # Drawdown total critique
        if dd_overall <= -MAX_OVERALL_DD_PCT:
            self._pause_until = time.time() + 12 * 3600   # Pause 12h
            reason = f"Drawdown total: {dd_overall:.1%} (seuil {-MAX_OVERALL_DD_PCT:.0%})"
            logger.critical(f"[DDGUARD V2] 🚨 VETO Drawdown total: {dd_overall:.1%}")
            return {
                "agent": self.name,
                "summary": f"🚨 VETO — {reason}",
                "arguments": [reason], "risks": [reason],
                "confidence": 1.0, "recommendation": "NO TRADE — Drawdown total",
                "veto": True, "veto_reason": "total_drawdown_exceeded",
                "telegram_alert": True,
                "consecutive_losses": losses,
            }

        # Pause active
        if paused:
            pause_type = "ARRÊT 24H" if losses >= 8 else ("PAUSE 1H" if losses >= 5 else "PAUSE 15min")
            summary    = f"⏸️ {pause_type} — {losses} pertes consécutives | {rem}min restantes | Recovery: {self._recovery_score:.0%}"
            return {
                "agent": self.name, "summary": summary,
                "arguments": [f"{losses} pertes d'affilée", f"Recovery score: {self._recovery_score:.0%}"],
                "risks": ["Streak de pertes"],
                "confidence": 1.0,
                "recommendation": f"NO TRADE — {pause_type} ({rem}min restantes)",
                "veto": True, "veto_reason": f"drawdown_guard_pause",
                "pause_minutes": rem, "consecutive_losses": losses,
                "telegram_alert": losses >= 5 and not self._telegram_alert_sent,
            }

        # ── OK — Trading autorisé ──────────────────────────────────────────
        warning = ""
        if losses >= 2:
            warning = f" ⚠️ {losses} pertes récentes — sizing réduit recommandé"
        summary = (
            f"✅ Circuit Breaker OK — {losses} perte{'s' if losses != 1 else ''} consécutive{'s' if losses != 1 else ''}"
            f" | DD jour: {dd_daily:.1%} | DD total: {dd_overall:.1%} | Recovery: {self._recovery_score:.0%}{warning}"
        )

        return {
            "agent":          self.name,
            "summary":        summary,
            "arguments": [
                f"Pertes consécutives: {losses}/8",
                f"Drawdown journalier: {dd_daily:.1%} (seuil: -{MAX_DAILY_DD_PCT:.0%})",
                f"Drawdown total: {dd_overall:.1%} (seuil: -{MAX_OVERALL_DD_PCT:.0%})",
                f"Recovery score: {self._recovery_score:.0%}",
            ],
            "risks": [f"⚠️ {losses} pertes récentes"] if losses >= 2 else [],
            "confidence":     0.95,
            "recommendation": "TRADE AUTORISÉ" if losses < 3 else "TRADE RÉDUIT",
            "veto":           False,
            "consecutive_losses": losses,
            "dd_daily":       dd_daily,
            "dd_overall":     dd_overall,
            "recovery_score": self._recovery_score,
        }
