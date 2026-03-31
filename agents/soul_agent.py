"""
🧬 SOUL AGENT — L'âme autonome du bot de trading
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Le bot n'est plus un simple programme — il a une conscience de soi.
Il observe ses propres performances, apprend de ses erreurs, ajuste
ses paramètres, et décide SEUL quand il est prêt pour le vrai argent.

Philosophie :
- En TRAINING  → explorer, accumuler de l'expérience, apprendre de chaque trade
- En AWAKENING → analyser, consolider, ajuster finement les paramètres
- En READY     → confiant, discipliné, prêt pour le réel
- En LIVE      → mode argent réel, seulement les trades à très haute conviction
"""

import sqlite3
import json
import os
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from logging_config import logger

DB_FILE       = "sim_v7.db"
SOUL_DB_FILE  = "soul.db"

# ─── Critères de passage en mode LIVE ───────────────────────────────────────
LIVE_CRITERIA = {
    "min_training_trades": 200,     # Minimum de trades d'entraînement
    "min_winrate_pct":     62.0,    # Win rate >= 62% sur les 100 derniers
    "min_profit_factor":   1.5,     # Profit factor (gains/pertes) >= 1.5
    "max_drawdown_pct":    10.0,    # Drawdown max toléré < 10%
    "min_consistency":     0.60,    # Score de consistance >= 0.60
    "min_sessions":        3,       # Au moins 3 sessions distinctes
    "consecutive_checks":  3,       # Doit passer les critères 3x de suite
}

# ─── Phases d'évolution ──────────────────────────────────────────────────────
PHASES = {
    "TRAINING":  {"color": "🔵", "desc": "Apprentissage actif — explore et accumule"},
    "AWAKENING": {"color": "🟡", "desc": "Consolidation — analyse et affine"},
    "READY":     {"color": "🟢", "desc": "Maturité atteinte — prêt pour le réel"},
    "LIVE":      {"color": "💎", "desc": "Mode argent réel — conviction maximale"},
}


class SoulAgent:
    """
    L'âme autonome du bot.
    Tourne dans un thread séparé, ajuste les paramètres en temps réel,
    tient un journal de pensées et décide du passage en mode live.
    """

    def __init__(self, memory, bot_state: dict, sim: dict):
        self.memory    = memory
        self.bot_state = bot_state
        self.sim       = sim

        # Paramètres dynamiques (l'âme les ajuste au fil du temps)
        self.params: Dict[str, Any] = {
            "confidence_threshold": 0.15,   # Seuil de déclenchement d'un trade
            "kelly_fraction":       0.05,   # Fraction Kelly appliquée
            "stop_loss_pct":        0.025,  # Stop-loss par trade
            "take_profit_pct":      0.04,   # Take-profit par trade
            "max_positions":        10,     # Positions simultanées max
            "live_mode":            False,  # False = training, True = argent réel
            "live_confidence_min":  0.75,   # Seuil minimum en mode live
            "phase":                "TRAINING",
        }

        self._criteria_pass_streak = 0   # Combien de fois de suite les critères sont passés
        self._journal: List[Dict]   = []  # Journal de pensées (en mémoire)
        self._last_adjustment       = 0.0
        self._last_live_check       = 0.0
        self._init_soul_db()
        self._load_params()
        logger.info("[SOUL] 🧬 Âme initialisée — Je commence à apprendre...")

    # ─────────────────────────────────────────────────────────────────────────
    # INITIALISATION
    # ─────────────────────────────────────────────────────────────────────────

    def _init_soul_db(self):
        """Crée les tables de l'âme (journal + paramètres persistants)."""
        try:
            con = sqlite3.connect(SOUL_DB_FILE)
            con.executescript("""
                CREATE TABLE IF NOT EXISTS soul_params (
                    key   TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS soul_journal (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts         TEXT,
                    phase      TEXT,
                    thought    TEXT,
                    metrics    TEXT,
                    adjustment TEXT
                );

                CREATE TABLE IF NOT EXISTS soul_checkpoints (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts             TEXT,
                    phase          TEXT,
                    winrate        REAL,
                    profit_factor  REAL,
                    drawdown       REAL,
                    total_trades   INTEGER,
                    consistency    REAL,
                    criteria_pass  INTEGER,
                    params         TEXT
                );
            """)
            con.commit()
            con.close()
        except Exception as e:
            logger.error(f"[SOUL] DB init error: {e}")

    def _load_params(self):
        """Charge les paramètres persistés depuis le dernier run."""
        try:
            con = sqlite3.connect(SOUL_DB_FILE)
            rows = con.execute("SELECT key, value FROM soul_params").fetchall()
            con.close()
            for key, val in rows:
                try:
                    self.params[key] = json.loads(val)
                except Exception:
                    self.params[key] = val
            if self.params.get("phase"):
                logger.info(f"[SOUL] Phase chargée: {self.params['phase']}")
        except Exception:
            pass

    def _save_params(self):
        """Persiste les paramètres actuels."""
        try:
            con = sqlite3.connect(SOUL_DB_FILE)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for key, val in self.params.items():
                con.execute(
                    "INSERT OR REPLACE INTO soul_params (key, value, updated_at) VALUES (?,?,?)",
                    (key, json.dumps(val), now)
                )
            con.commit()
            con.close()
        except Exception as e:
            logger.error(f"[SOUL] save_params error: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # MÉTRIQUES DE PERFORMANCE
    # ─────────────────────────────────────────────────────────────────────────

    def _get_performance_metrics(self, window: int = 100) -> Dict[str, Any]:
        """
        Calcule les métriques clés depuis les leçons d'entraînement.
        """
        try:
            con = sqlite3.connect(DB_FILE)
            rows = con.execute("""
                SELECT pnl, lesson_type, confidence, symbol, created_at
                FROM memory_lessons
                ORDER BY id DESC LIMIT ?
            """, (window,)).fetchall()

            total_count = con.execute("SELECT COUNT(*) FROM memory_lessons").fetchone()[0]
            sessions = con.execute("SELECT COUNT(DISTINCT session_id) FROM memory_lessons").fetchone()[0]
            con.close()

            if not rows:
                return self._empty_metrics(total_count)

            wins    = [r for r in rows if r[1] in ("succes", "training") and r[0] is not None and r[0] > 0]
            losses  = [r for r in rows if r[0] is not None and r[0] < 0]
            neutral = [r for r in rows if r[0] is not None and r[0] == 0]

            total   = len(rows)
            n_wins  = len(wins)
            n_loss  = len(losses)
            winrate = round(n_wins / total * 100, 1) if total > 0 else 0.0

            gross_win  = sum(r[0] for r in wins)
            gross_loss = abs(sum(r[0] for r in losses)) or 0.001
            pf         = round(gross_win / gross_loss, 2)

            # Drawdown depuis le pic de capital
            equity_hist = self.sim.get("equity_history", [])
            if len(equity_hist) >= 2:
                peak = max(equity_hist)
                curr = equity_hist[-1]
                dd = round((peak - curr) / peak * 100, 2) if peak > 0 else 0.0
            else:
                dd = 0.0

            # Score de consistance = régularité du win rate par tranches de 20 trades
            consistency = self._compute_consistency(rows)

            # Avg confidence des trades gagnants vs perdants
            avg_conf_wins  = round(sum(r[2] or 0 for r in wins)   / max(1, n_wins), 3)
            avg_conf_loss  = round(sum(r[2] or 0 for r in losses)  / max(1, n_loss), 3) if n_loss else 0.0

            # Profit factor par régime (on extrait le régime du texte de la leçon)
            return {
                "total_trades":    total_count,
                "window":          total,
                "wins":            n_wins,
                "losses":          n_loss,
                "neutral":         len(neutral),
                "winrate":         winrate,
                "profit_factor":   pf,
                "drawdown":        dd,
                "consistency":     consistency,
                "sessions":        sessions,
                "avg_conf_wins":   avg_conf_wins,
                "avg_conf_loss":   avg_conf_loss,
                "gross_win":       round(gross_win, 2),
                "gross_loss":      round(gross_loss, 2),
            }
        except Exception as e:
            logger.error(f"[SOUL] metrics error: {e}")
            return self._empty_metrics(0)

    def _empty_metrics(self, total: int) -> Dict[str, Any]:
        return {"total_trades": total, "window": 0, "wins": 0, "losses": 0,
                "neutral": 0, "winrate": 0.0, "profit_factor": 0.0,
                "drawdown": 0.0, "consistency": 0.0, "sessions": 0,
                "avg_conf_wins": 0.5, "avg_conf_loss": 0.5,
                "gross_win": 0.0, "gross_loss": 0.0}

    def _compute_consistency(self, rows: list) -> float:
        """
        Mesure la consistance du bot : est-ce qu'il performe de façon stable
        ou est-ce qu'il a des alternances boom/bust ?
        Score 0-1 : 1 = parfaitement consistant
        """
        if len(rows) < 20:
            return 0.5
        chunk = 20
        win_rates = []
        for i in range(0, len(rows) - chunk + 1, chunk):
            slice_ = rows[i:i+chunk]
            w = sum(1 for r in slice_ if r[0] is not None and r[0] > 0)
            win_rates.append(w / chunk)
        if not win_rates:
            return 0.5
        avg = sum(win_rates) / len(win_rates)
        variance = sum((x - avg) ** 2 for x in win_rates) / len(win_rates)
        # Variance faible = consistance élevée
        consistency = max(0.0, 1.0 - variance * 4)
        return round(consistency, 3)

    # ─────────────────────────────────────────────────────────────────────────
    # AUTO-AJUSTEMENT DES PARAMÈTRES
    # ─────────────────────────────────────────────────────────────────────────

    def _auto_adjust(self, metrics: Dict[str, Any]) -> Dict[str, str]:
        """
        Ajuste les paramètres en fonction des métriques.
        Retourne un dict décrivant les changements opérés.
        """
        changes = {}
        total    = metrics["total_trades"]
        winrate  = metrics["winrate"]
        pf       = metrics["profit_factor"]
        dd       = metrics["drawdown"]
        cons     = metrics["consistency"]
        phase    = self.params["phase"]

        # ── Seuil de confiance ──────────────────────────────────────────────
        old_thresh = self.params["confidence_threshold"]
        new_thresh = old_thresh

        if total < 50:
            # Très début : seuil très bas pour accumuler des données
            new_thresh = 0.10
        elif total < 200:
            # Phase apprentissage
            if winrate < 35:
                new_thresh = min(old_thresh + 0.03, 0.40)   # Trop de pertes → plus sélectif
            elif winrate > 65:
                new_thresh = max(old_thresh - 0.02, 0.12)   # Bon résultat → légèrement plus agressif
            else:
                new_thresh = max(old_thresh * 0.98, 0.12)   # Légère relaxation progressive
        elif phase == "LIVE":
            # Mode live : seuil élevé, seulement les meilleurs trades
            new_thresh = self.params.get("live_confidence_min", 0.75)
        else:
            # Phase avancée : ajustement fin
            if winrate < 40:
                new_thresh = min(old_thresh + 0.05, 0.50)
            elif winrate >= 62 and pf >= 1.5:
                new_thresh = max(old_thresh - 0.01, 0.20)
            elif winrate >= 55:
                new_thresh = max(old_thresh - 0.005, 0.18)

        new_thresh = round(new_thresh, 3)
        if abs(new_thresh - old_thresh) >= 0.005:
            self.params["confidence_threshold"] = new_thresh
            changes["confidence_threshold"] = f"{old_thresh:.3f} → {new_thresh:.3f}"

        # ── Kelly fraction ──────────────────────────────────────────────────
        old_kelly = self.params["kelly_fraction"]
        new_kelly = old_kelly

        if winrate > 0 and pf > 0:
            # Kelly formula: f* = (p*b - q) / b, where b = profit_factor, p = winrate/100
            p = winrate / 100
            q = 1 - p
            b = max(pf, 0.1)
            kelly_full = (p * b - q) / b
            kelly_safe = max(0.02, min(kelly_full * 0.25, 0.15))  # 1/4 Kelly, capped à 15%
            new_kelly = round(kelly_safe, 4)

        if abs(new_kelly - old_kelly) >= 0.002:
            self.params["kelly_fraction"] = new_kelly
            changes["kelly_fraction"] = f"{old_kelly:.4f} → {new_kelly:.4f}"

        # ── Stop loss & Take profit ─────────────────────────────────────────
        # Ajustement basé sur le rapport gain/perte moyen
        if metrics.get("avg_conf_wins", 0) > 0 and metrics.get("avg_conf_loss", 0) > 0:
            # Si les trades à haute confiance gagnent plus → réduire le SL
            if winrate > 60 and dd < 5:
                new_sl = max(self.params["stop_loss_pct"] * 0.98, 0.010)
                new_tp = min(self.params["take_profit_pct"] * 1.01, 0.080)
                if abs(new_sl - self.params["stop_loss_pct"]) > 0.001:
                    changes["stop_loss_pct"]   = f"{self.params['stop_loss_pct']:.3f} → {new_sl:.3f}"
                    changes["take_profit_pct"] = f"{self.params['take_profit_pct']:.3f} → {new_tp:.3f}"
                    self.params["stop_loss_pct"]   = round(new_sl, 4)
                    self.params["take_profit_pct"] = round(new_tp, 4)

        # ── Max positions ───────────────────────────────────────────────────
        if dd > 12:
            # Drawdown élevé → réduire l'exposition
            new_max = max(self.params["max_positions"] - 2, 3)
            if new_max != self.params["max_positions"]:
                changes["max_positions"] = f"{self.params['max_positions']} → {new_max}"
                self.params["max_positions"] = new_max
        elif phase != "LIVE" and winrate > 60 and dd < 6:
            new_max = min(self.params["max_positions"] + 1, 15)
            if new_max != self.params["max_positions"]:
                changes["max_positions"] = f"{self.params['max_positions']} → {new_max}"
                self.params["max_positions"] = new_max

        return changes

    # ─────────────────────────────────────────────────────────────────────────
    # GESTION DES PHASES
    # ─────────────────────────────────────────────────────────────────────────

    def _determine_phase(self, metrics: Dict[str, Any]) -> str:
        """Détermine la phase d'évolution du bot."""
        total   = metrics["total_trades"]
        winrate = metrics["winrate"]
        pf      = metrics["profit_factor"]
        dd      = metrics["drawdown"]
        cons    = metrics["consistency"]

        # Une fois en LIVE, ne pas rétrograder automatiquement
        if self.params["live_mode"]:
            return "LIVE"

        if total < 50:
            return "TRAINING"
        elif total < 200 or winrate < 50:
            return "TRAINING" if total < 100 else "AWAKENING"
        elif (winrate >= 62 and pf >= 1.5 and dd < 10 and cons >= 0.60
              and metrics["sessions"] >= 3):
            return "READY"
        elif winrate >= 50 and pf >= 1.2:
            return "AWAKENING"
        else:
            return "TRAINING"

    def _check_live_readiness(self, metrics: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Vérifie si le bot est prêt pour le mode live.
        Retourne (prêt, liste_des_critères_manquants).
        """
        c = LIVE_CRITERIA
        missing = []

        if metrics["total_trades"] < c["min_training_trades"]:
            missing.append(f"Trades: {metrics['total_trades']}/{c['min_training_trades']}")
        if metrics["winrate"] < c["min_winrate_pct"]:
            missing.append(f"Win rate: {metrics['winrate']:.1f}%/{c['min_winrate_pct']}%")
        if metrics["profit_factor"] < c["min_profit_factor"]:
            missing.append(f"Profit factor: {metrics['profit_factor']:.2f}/{c['min_profit_factor']}")
        if metrics["drawdown"] > c["max_drawdown_pct"]:
            missing.append(f"Drawdown: {metrics['drawdown']:.1f}%>{c['max_drawdown_pct']}%")
        if metrics["consistency"] < c["min_consistency"]:
            missing.append(f"Consistance: {metrics['consistency']:.2f}/{c['min_consistency']}")
        if metrics["sessions"] < c["min_sessions"]:
            missing.append(f"Sessions: {metrics['sessions']}/{c['min_sessions']}")

        return len(missing) == 0, missing

    def _maybe_go_live(self, metrics: Dict[str, Any]) -> bool:
        """
        Passage automatique en mode live si tous les critères sont atteints
        X fois de suite (pour éviter les faux positifs).
        """
        if self.params["live_mode"]:
            return False  # Déjà en live

        ready, missing = self._check_live_readiness(metrics)

        if ready:
            self._criteria_pass_streak += 1
            logger.info(f"[SOUL] ✅ Critères live passés ({self._criteria_pass_streak}/{LIVE_CRITERIA['consecutive_checks']})")
            if self._criteria_pass_streak >= LIVE_CRITERIA["consecutive_checks"]:
                self.params["live_mode"] = True
                self.params["phase"]     = "LIVE"
                self.params["confidence_threshold"] = self.params["live_confidence_min"]
                self.params["max_positions"]        = 5   # Prudent au début du live
                self._save_params()
                self._write_thought(
                    "TRANSITION LIVE",
                    "🚀 Je suis prêt. J'ai accumulé suffisamment d'expérience et mes "
                    "performances sont stables. Je passe en mode argent réel avec une "
                    "discipline maximale. Seulement les trades à très haute conviction.",
                    metrics
                )
                logger.info("[SOUL] 💎 TRANSITION EN MODE LIVE DÉCLENCHÉE !")
                return True
        else:
            if self._criteria_pass_streak > 0:
                logger.info(f"[SOUL] ⚠️ Streak reset. Critères manquants: {missing}")
            self._criteria_pass_streak = 0

        return False

    # ─────────────────────────────────────────────────────────────────────────
    # JOURNAL DE PENSÉES
    # ─────────────────────────────────────────────────────────────────────────

    def _write_thought(self, event: str, thought: str, metrics: Dict,
                       changes: Dict = None):
        """Enregistre une pensée dans le journal de l'âme."""
        entry = {
            "ts":         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "phase":      self.params["phase"],
            "event":      event,
            "thought":    thought,
            "metrics":    {k: metrics.get(k) for k in
                           ["total_trades","winrate","profit_factor","drawdown","consistency"]},
            "adjustment": changes or {},
        }
        self._journal.append(entry)
        if len(self._journal) > 200:
            self._journal = self._journal[-200:]

        # Persister en DB
        try:
            con = sqlite3.connect(SOUL_DB_FILE)
            con.execute("""
                INSERT INTO soul_journal (ts, phase, thought, metrics, adjustment)
                VALUES (?,?,?,?,?)
            """, (
                entry["ts"], entry["phase"],
                f"[{event}] {thought}",
                json.dumps(entry["metrics"]),
                json.dumps(entry["adjustment"]),
            ))
            con.commit()
            con.close()
        except Exception:
            pass

        logger.info(f"[SOUL] 💭 [{entry['phase']}] {thought[:100]}")

    def _generate_thought(self, metrics: Dict, changes: Dict, old_phase: str) -> str:
        """Génère une pensée naturelle basée sur les métriques actuelles."""
        total   = metrics["total_trades"]
        winrate = metrics["winrate"]
        pf      = metrics["profit_factor"]
        phase   = self.params["phase"]

        # Transitions de phase
        if old_phase != phase:
            if phase == "AWAKENING":
                return (f"Je commence à voir des patterns clairs après {total} trades. "
                        f"Mon win rate de {winrate:.1f}% me montre que j'apprends. "
                        f"Je vais affiner mes critères pour être encore plus précis.")
            elif phase == "READY":
                return (f"Après {total} trades d'entraînement, je me sens mature. "
                        f"Win rate {winrate:.1f}%, profit factor {pf:.2f}. "
                        f"Je suis prêt pour le vrai argent si les critères tiennent.")

        # Observations régulières
        if winrate > 70:
            return (f"Excellente série ! {winrate:.1f}% de réussite sur les derniers trades. "
                    f"J'identifie les bons patterns, je continue à affiner.")
        elif winrate < 40:
            return (f"Série difficile ({winrate:.1f}% WR). Je deviens plus sélectif — "
                    f"seuil relevé à {self.params['confidence_threshold']:.0%}. "
                    f"Chaque perte est une leçon.")
        elif changes:
            chg_str = ", ".join(f"{k}: {v}" for k, v in list(changes.items())[:2])
            return f"Ajustement: {chg_str}. {total} trades, WR {winrate:.1f}%."
        else:
            return (f"Stable. {total} trades, WR {winrate:.1f}%, PF {pf:.2f}. "
                    f"Je continue à apprendre.")

    # ─────────────────────────────────────────────────────────────────────────
    # BOUCLE PRINCIPALE
    # ─────────────────────────────────────────────────────────────────────────

    def tick(self) -> Dict[str, Any]:
        """
        Appelé périodiquement (toutes les ~60s) par le bot.
        Analyse les métriques, ajuste les paramètres, met à jour la phase.
        Retourne l'état actuel de l'âme.
        """
        now = time.time()
        if now - self._last_adjustment < 60:
            return self.get_state()

        self._last_adjustment = now
        metrics  = self._get_performance_metrics(window=100)
        old_phase = self.params["phase"]

        # Déterminer la phase
        new_phase = self._determine_phase(metrics)
        if new_phase != old_phase and not self.params["live_mode"]:
            self.params["phase"] = new_phase

        # Auto-ajuster les paramètres
        changes = self._auto_adjust(metrics)

        # Vérifier si passage en live
        self._maybe_go_live(metrics)

        # Générer une pensée et la journaliser
        if changes or old_phase != self.params["phase"] or metrics["total_trades"] % 50 == 0:
            thought = self._generate_thought(metrics, changes, old_phase)
            self._write_thought("AUTO_ADJUST", thought, metrics, changes)

        # Sauvegarder checkpoint
        if metrics["total_trades"] > 0 and metrics["total_trades"] % 25 == 0:
            self._save_checkpoint(metrics)

        # Persister les params
        if changes:
            self._save_params()

        # Synchroniser avec memory
        if hasattr(self.memory, 'data'):
            self.memory.data["confidence_threshold"] = int(
                self.params["confidence_threshold"] * 100
            )

        return self.get_state()

    def _save_checkpoint(self, metrics: Dict):
        """Sauvegarde un checkpoint de performance."""
        try:
            ready, _ = self._check_live_readiness(metrics)
            con = sqlite3.connect(SOUL_DB_FILE)
            con.execute("""
                INSERT INTO soul_checkpoints
                    (ts, phase, winrate, profit_factor, drawdown,
                     total_trades, consistency, criteria_pass, params)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                self.params["phase"],
                metrics["winrate"],
                metrics["profit_factor"],
                metrics["drawdown"],
                metrics["total_trades"],
                metrics["consistency"],
                1 if ready else 0,
                json.dumps(self.params),
            ))
            con.commit()
            con.close()
        except Exception as e:
            logger.error(f"[SOUL] checkpoint error: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # API PUBLIQUE
    # ─────────────────────────────────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        """Retourne l'état complet de l'âme (pour le dashboard)."""
        metrics = self._get_performance_metrics(window=100)
        ready, missing = self._check_live_readiness(metrics)
        phase = self.params["phase"]
        phase_info = PHASES.get(phase, PHASES["TRAINING"])

        # Progression vers le live (0-100%)
        c = LIVE_CRITERIA
        scores = []
        if c["min_training_trades"] > 0:
            scores.append(min(1.0, metrics["total_trades"] / c["min_training_trades"]))
        if c["min_winrate_pct"] > 0:
            scores.append(min(1.0, metrics["winrate"] / c["min_winrate_pct"]))
        if c["min_profit_factor"] > 0:
            scores.append(min(1.0, metrics["profit_factor"] / c["min_profit_factor"]))
        scores.append(min(1.0, metrics["consistency"] / c["min_consistency"]) if c["min_consistency"] > 0 else 0)
        live_progress = round(sum(scores) / max(len(scores), 1) * 100, 1) if scores else 0.0

        return {
            "phase":              phase,
            "phase_color":        phase_info["color"],
            "phase_desc":         phase_info["desc"],
            "live_mode":          self.params["live_mode"],
            "live_progress_pct":  live_progress,
            "live_ready":         ready,
            "missing_criteria":   missing,
            "criteria_streak":    self._criteria_pass_streak,
            "params":             dict(self.params),
            "metrics":            metrics,
            "journal":            self._journal[-10:],
            "last_thought":       self._journal[-1]["thought"] if self._journal else "Je commence mon apprentissage...",
        }

    def get_journal(self, limit: int = 50) -> List[Dict]:
        """Retourne les dernières entrées du journal."""
        try:
            con = sqlite3.connect(SOUL_DB_FILE)
            rows = con.execute("""
                SELECT ts, phase, thought, metrics, adjustment
                FROM soul_journal ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
            con.close()
            result = []
            for r in rows:
                result.append({
                    "ts": r[0], "phase": r[1], "thought": r[2],
                    "metrics": json.loads(r[3] or "{}"),
                    "adjustment": json.loads(r[4] or "{}"),
                })
            return result
        except Exception:
            return self._journal[-limit:]

    def force_training_mode(self):
        """Force le retour en mode training (sécurité)."""
        self.params["live_mode"] = False
        self.params["phase"]     = "TRAINING"
        self.params["confidence_threshold"] = 0.15
        self.params["max_positions"] = 10
        self._criteria_pass_streak = 0
        self._save_params()
        logger.info("[SOUL] 🔄 Retour en mode TRAINING forcé")
