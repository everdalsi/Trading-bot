"""
🔧 SELF IMPROVEMENT AGENT V3 — Watchdog + Santé Système + Auto-Réparation
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Rôle : monitore la santé de tous les agents, détecte les crashs, répare
les incohérences, et remonte un score de santé global au cerveau collectif.
Répond au mot-clé "monitor health" envoyé par l'Orchestrator.
"""

import time
import sqlite3
import os
from datetime import datetime
from typing import Dict, Any, List
from agents.base_agent import BaseAgent
from logging_config import logger

DB_FILE = "sim_v7.db"


class SelfImprovementAgent(BaseAgent):
    """
    Agent de surveillance et d'auto-réparation du système multi-agents.
    Répond aux questions de type 'monitor health', 'watchdog', 'santé système'.
    """

    def __init__(self, orchestrator=None):
        super().__init__(
            name="self_improvement",
            role="Surveillance santé système, watchdog, détection anomalies et auto-réparation"
        )
        self.orchestrator = orchestrator
        self._last_health_check = 0
        self._health_cache: Dict[str, Any] = {}
        self._anomaly_log: List[str] = []

    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        keywords = [
            "monitor", "health", "santé", "watchdog", "immune",
            "repair", "répare", "anomalie", "crash", "erreur système",
            "self_improvement", "amélioration", "auto-répar", "surveillance",
            "synthèse", "débat", "cerveau collectif", "final decision", "raffine"
        ]
        return any(kw in q for kw in keywords)

    def _check_db_health(self) -> Dict[str, Any]:
        """Vérifie l'intégrité de la base de données SQLite."""
        try:
            con = sqlite3.connect(DB_FILE)
            lesson_count = con.execute("SELECT COUNT(*) FROM memory_lessons").fetchone()[0]
            pattern_count = con.execute("SELECT COUNT(*) FROM memory_patterns").fetchone()[0]
            insight_count = con.execute("SELECT COUNT(*) FROM memory_insights WHERE active=1").fetchone()[0]
            con.close()
            return {
                "ok": True,
                "lessons": lesson_count,
                "patterns": pattern_count,
                "insights": insight_count
            }
        except Exception as e:
            logger.error(f"❌ [SELF-IMPROVEMENT] DB health check failed: {e}")
            return {"ok": False, "error": str(e), "lessons": 0, "patterns": 0, "insights": 0}

    def _check_agent_timeouts(self, context: dict) -> List[str]:
        """Détecte les agents ayant eu un timeout dans la dernière réponse."""
        timeouts = []
        agent_outputs = context.get("agent_outputs", [])
        for resp in agent_outputs:
            if isinstance(resp, dict) and resp.get("error") == "timeout":
                timeouts.append(resp.get("agent", "unknown"))
        return timeouts

    def _check_confidence_anomaly(self, context: dict) -> bool:
        """Détecte si la confiance collective est anormalement basse."""
        agent_outputs = context.get("agent_outputs", [])
        if not agent_outputs:
            return False
        confs = [r.get("confidence", 0) for r in agent_outputs if isinstance(r, dict)]
        if not confs:
            return False
        avg_conf = sum(confs) / len(confs)
        return avg_conf < 0.3

    def _compute_health_score(
        self,
        db_health: Dict,
        timeouts: List[str],
        conf_anomaly: bool
    ) -> float:
        """Calcule un score de santé global entre 0 et 100."""
        score = 100.0
        if not db_health["ok"]:
            score -= 40
        timeout_penalty = min(len(timeouts) * 10, 30)
        score -= timeout_penalty
        if conf_anomaly:
            score -= 20
        return max(0.0, min(100.0, score))

    def _log_anomaly(self, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._anomaly_log.append(f"[{ts}] {message}")
        if len(self._anomaly_log) > 50:
            self._anomaly_log = self._anomaly_log[-50:]
        logger.warning(f"[SELF-IMPROVEMENT] ANOMALIE: {message}")

    def get_anomaly_log(self) -> List[str]:
        return list(self._anomaly_log)

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {
                "agent": self.name,
                "summary": f"⚠️ {self.name} hors spécialité → ignoré",
                "confidence": 0.0,
                "recommendation": "HOLD - Vérifier rôle",
                "warning": "Hors domaine self_improvement"
            }

        now = time.time()
        cache_ttl = 30
        if now - self._last_health_check < cache_ttl and self._health_cache:
            return self._health_cache

        db_health = self._check_db_health()
        timeouts = self._check_agent_timeouts(context)
        conf_anomaly = self._check_confidence_anomaly(context)
        health_score = self._compute_health_score(db_health, timeouts, conf_anomaly)

        if timeouts:
            self._log_anomaly(f"Agents en timeout: {', '.join(timeouts)}")
        if conf_anomaly:
            self._log_anomaly("Confiance collective anormalement basse (<30%)")
        if not db_health["ok"]:
            self._log_anomaly(f"DB SQLite inaccessible: {db_health.get('error', '?')}")

        status_emoji = "✅" if health_score >= 80 else "⚠️" if health_score >= 50 else "❌"
        summary = (
            f"{status_emoji} Santé système: {health_score:.0f}/100 | "
            f"DB={'OK' if db_health['ok'] else 'KO'} ({db_health['lessons']} leçons) | "
            f"Timeouts={len(timeouts)} | ConfAnomalie={'Oui' if conf_anomaly else 'Non'}"
        )

        result = {
            "agent": self.name,
            "summary": summary,
            "score": health_score,
            "confidence": min(health_score / 100, 0.99),
            "recommendation": "HOLD - Système dégradé" if health_score < 50 else "OK",
            "db_health": db_health,
            "timeouts": timeouts,
            "confidence_anomaly": conf_anomaly,
            "anomaly_log": self._anomaly_log[-5:],
        }

        self._health_cache = result
        self._last_health_check = now
        logger.info(f"[SELF-IMPROVEMENT] Health check terminé → {health_score:.0f}/100")
        return result
