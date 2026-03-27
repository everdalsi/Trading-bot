import sqlite3
import json
import time
import os
from datetime import datetime
from agents.base_agent import BaseAgent
from typing import Dict, Any, List
from logging_config import logger

DB_FILE = "sim_v7.db"
KNOWLEDGE_DIR = "knowledge"


class LearningAgent(BaseAgent):

    def __init__(self):
        super().__init__(
            name="learning",
            role="Mémoire infinie, scoring des patterns et ajustement de confiance"
        )
        self._ensure_tables()
        self.knowledge_text = self._load_knowledge_base()

    def _ensure_tables(self):
        try:
            con = sqlite3.connect(DB_FILE)
            con.execute("""
                CREATE TABLE IF NOT EXISTS memory_lessons (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id    INTEGER,
                    symbol      TEXT,
                    market      TEXT DEFAULT 'SPOT',
                    pnl         REAL,
                    pnl_pct     REAL,
                    lesson_type TEXT,
                    lecon       TEXT,
                    pattern     TEXT,
                    action      TEXT,
                    confidence  REAL DEFAULT 0.5,
                    tags        TEXT,
                    created_at  TEXT,
                    session_id  INTEGER DEFAULT 1
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS memory_insights (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    insight     TEXT,
                    score       REAL,
                    source_count INTEGER,
                    symbol      TEXT DEFAULT 'GLOBAL',
                    created_at  TEXT,
                    active      INTEGER DEFAULT 1
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS memory_patterns (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern     TEXT UNIQUE,
                    symbol      TEXT DEFAULT 'GLOBAL',
                    occurrences INTEGER DEFAULT 1,
                    wins        INTEGER DEFAULT 0,
                    losses      INTEGER DEFAULT 0,
                    win_rate    REAL DEFAULT 0.5,
                    last_seen   TEXT,
                    is_rule     INTEGER DEFAULT 0
                )
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_lessons_symbol
                ON memory_lessons(symbol)
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_lessons_type
                ON memory_lessons(lesson_type)
            """)
            con.commit()
            con.close()
        except Exception as e:
            logger.error(f"❌ [LEARNING-DB] Init error: {e}")

    def _load_knowledge_base(self) -> str:
        """Charge tous les PDFs du dossier knowledge/ ou racine (Fix Railway)"""
        knowledge = ""
        
        # --- DÉTECTION DES CHEMINS ---
        search_paths = [
            os.path.join(os.getcwd(), KNOWLEDGE_DIR),
            "/workspace/knowledge",
            os.getcwd() 
        ]
        
        target_path = None
        for p in search_paths:
            if os.path.exists(p):
                pdfs = [f for f in os.listdir(p) if f.lower().endswith(".pdf")]
                if pdfs:
                    target_path = p
                    break
        
        if not target_path:
            logger.warning(f"⚠️ [KNOWLEDGE] Aucun PDF trouvé dans {search_paths}")
            return knowledge

        logger.info(f"📖 [KNOWLEDGE] Chargement depuis : {target_path}")
        for filename in os.listdir(target_path):
            if filename.lower().endswith(".pdf"):
                path = os.path.join(target_path, filename)
                try:
                    from pypdf import PdfReader
                    reader = PdfReader(path)
                    text = ""
                    for page in reader.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text += page_text + "\n"
                    knowledge += f"\n\n--- {filename.upper()} ---\n{text}\n"
                    logger.info(f"✅ [KNOWLEDGE] Chargé : {filename} ({len(text)} caractères)")
                except Exception as e:
                    logger.error(f"❌ [KNOWLEDGE] Erreur lecture {filename}: {e}")
        
        if knowledge:
            logger.info(f"🧠 [KNOWLEDGE] Base de connaissance pro chargée ({len(knowledge)} caractères total)")
        return knowledge

    def save_lesson(self, lesson: dict) -> int:
        try:
            con = sqlite3.connect(DB_FILE)
            cur = con.execute("""
                INSERT INTO memory_lessons
                    (trade_id, symbol, market, pnl, pnl_pct, lesson_type,
                     lecon, pattern, action, confidence, tags, created_at, session_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                lesson.get("trade_id"),
                lesson.get("symbol", "UNKNOWN"),
                lesson.get("market", "SPOT"),
                lesson.get("pnl", 0.0),
                lesson.get("pnl_pct", 0.0),
                lesson.get("type", "erreur"),
                lesson.get("lecon", ""),
                lesson.get("pattern", ""),
                lesson.get("action_future", ""),
                lesson.get("confidence", 0.5),
                json.dumps(lesson.get("tags", [])),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                lesson.get("session_id", 1),
            ))
            lesson_id = cur.lastrowid
            con.commit()
            con.close()

            self._update_pattern(
                lesson.get("pattern", ""),
                lesson.get("symbol", "GLOBAL"),
                lesson.get("type") == "succes"
            )
            return lesson_id
        except Exception as e:
            logger.error(f"❌ [LEARNING-DB] save_lesson error: {e}")
            return -1

    def _update_pattern(self, pattern: str, symbol: str, is_win: bool):
        if not pattern:
            return
        try:
            con = sqlite3.connect(DB_FILE)
            row = con.execute(
                "SELECT id, occurrences, wins, losses FROM memory_patterns WHERE pattern=?",
                (pattern,)
            ).fetchone()

            if row:
                occ  = row[1] + 1
                wins = row[2] + (1 if is_win else 0)
                loss = row[3] + (0 if is_win else 1)
                wr   = wins / occ
                is_rule = 1 if occ >= 3 else 0
                con.execute("""
                    UPDATE memory_patterns
                    SET occurrences=?, wins=?, losses=?, win_rate=?,
                        last_seen=?, is_rule=?
                    WHERE id=?
                """, (occ, wins, loss, wr,
                      datetime.now().strftime("%Y-%m-%d %H:%M"), is_rule,
                      row[0]))
            else:
                con.execute("""
                    INSERT INTO memory_patterns
                        (pattern, symbol, occurrences, wins, losses, win_rate, last_seen)
                    VALUES (?,?,1,?,?,?,?)
                """, (pattern, symbol,
                      1 if is_win else 0,
                      0 if is_win else 1,
                      1.0 if is_win else 0.0,
                      datetime.now().strftime("%Y-%m-%d %H:%M")))
            con.commit()
            con.close()
        except Exception as e:
            logger.error(f"❌ [LEARNING-DB] update_pattern error: {e}")

    def get_lesson_count(self) -> int:
        try:
            con = sqlite3.connect(DB_FILE)
            count = con.execute("SELECT COUNT(*) FROM memory_lessons").fetchone()[0]
            con.close()
            return count
        except Exception:
            return 0

    def get_symbol_stats_db(self, symbol: str, window: int = 20) -> dict:
        try:
            con = sqlite3.connect(DB_FILE)
            rows = con.execute("""
                SELECT pnl, pnl_pct, lesson_type, confidence
                FROM memory_lessons
                WHERE symbol = ?
                ORDER BY id DESC LIMIT ?
            """, (symbol, window)).fetchall()
            con.close()

            if not rows:
                return {"score": 0.5, "count": 0, "wins": 0, "losses": 0, "avg_pnl": 0.0}

            wins = sum(1 for r in rows if r[2] == "succes")
            losses = len(rows) - wins
            score = round(wins / len(rows), 3)
            avg_pnl = round(sum(r[0] for r in rows if r[0] is not None) / len(rows), 4)

            return {
                "score": score,
                "count": len(rows),
                "wins": wins,
                "losses": losses,
                "avg_pnl": avg_pnl
            }
        except Exception as e:
            logger.error(f"❌ [LEARNING] get_symbol_stats_db error: {e}")
            return {"score": 0.5, "count": 0, "wins": 0, "losses": 0, "avg_pnl": 0.0}

    # === UPGRADE ÉTAPE 3 : NOUVELLE MÉTHODE POST-TRADE ANALYSIS + AUTO-VETO ===
    async def analyze_trade_outcome(self, trade_result: dict, context: dict) -> Dict[str, Any]:
        """Analyse post-trade ultra-agressive : détecte perdant/gagnant et déclenche blacklist/veto"""
        symbol = trade_result.get("symbol", "UNKNOWN")
        pnl_pct = trade_result.get("pnl_pct", 0.0)
        lesson_type = "succes" if pnl_pct > 0 else "echec"

        lesson = {
            "trade_id": trade_result.get("id"),
            "symbol": symbol,
            "pnl": trade_result.get("pnl", 0.0),
            "pnl_pct": pnl_pct,
            "type": lesson_type,
            "lecon": trade_result.get("reason", "Trade analysé post-mortem"),
            "pattern": trade_result.get("pattern", ""),
            "action_future": "NEVER_REPEAT" if pnl_pct < 0 else "REPEAT_WITH_HIGHER_CONFIDENCE",
            "tags": ["post_trade_analysis", "veto_candidate" if pnl_pct < 0 else "good_pattern"]
        }

        lesson_id = self.save_lesson(lesson)
        logger.info(f"[LEARNING] Post-trade analysis #{lesson_id} → {lesson_type.upper()} sur {symbol} ({pnl_pct:+.2f}%)")

        # VETO IMMÉDIAT si trade perdant
        if pnl_pct < 0:
            self._add_to_blacklist(symbol, reason=f"Trade perdant détecté (-{abs(pnl_pct):.2f}%)")
            return {
                "agent": self.name,
                "blacklist": True,
                "summary": f"TRADE PERDANT DÉTECTÉ → blacklist + veto Learning activé sur {symbol}",
                "recommendation": "NO TRADE futur sur ce pattern/symbole",
                "confidence": 1.0
            }

        return {"agent": self.name, "blacklist": False, "summary": "Trade gagnant → leçon positive intégrée", "confidence": 0.95}

    def _add_to_blacklist(self, symbol: str, reason: str):
        """Blacklist renforcée (persistante)"""
        try:
            con = sqlite3.connect(DB_FILE)
            con.execute("""
                INSERT OR REPLACE INTO memory_patterns (pattern, symbol, occurrences, wins, losses, win_rate, last_seen)
                VALUES (?, ?, 999, 0, 999, 0.0, ?)
            """, (f"BLACKLIST_{symbol}", symbol, datetime.now().strftime("%Y-%m-%d %H:%M")))
            con.commit()
            con.close()
            logger.warning(f"[LEARNING] BLACKLIST ajouté : {symbol} → {reason}")
        except Exception as e:
            logger.error(f"❌ Blacklist error: {e}")

    # === UPGRADE ÉTAPE 3 : AUTO-ÉVALUATION TOUTES LES 100 TRADES + APPEL SELF-IMPROVEMENT ===
    async def auto_evaluate_and_improve(self, context: dict):
        """Boucle auto-évaluation : toutes les 100 trades → appelle l'agent ingénieur pour modifier le code"""
        lesson_count = self.get_lesson_count()
        if lesson_count % 100 == 0 and lesson_count > 0:
            logger.info(f"[LEARNING] 🔥 AUTO-ÉVALUATION déclenchée après {lesson_count} trades ! Appel SelfImprovementEngineer...")
            
            # Appel direct de l'agent ingénieur pour qu'il modifie lui-même le code
            if hasattr(context.get("orchestrator"), "self_improvement"):
                improvement_ctx = {
                    **context,
                    "lesson_count": lesson_count,
                    "trigger": "auto_evaluation_100_trades",
                    "strict_veto_mode": True
                }
                await context["orchestrator"].self_improvement.respond(
                    "Analyse les 100 derniers trades et modifie le code pour améliorer winrate (ajoute veto, ajuste seuils, renforce blacklist)", 
                    improvement_ctx
                )
            else:
                logger.warning("[LEARNING] SelfImprovement non trouvé dans orchestrator")

        return {"lessons_analyzed": lesson_count, "auto_improvement_triggered": lesson_count % 100 == 0}

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        # === UPGRADE ÉTAPE 3 : Appel auto-évaluation + post-trade si applicable ===
        if "trade_result" in context or "post_trade" in question.lower():
            trade_result = context.get("trade_result", {})
            if trade_result:
                return await self.analyze_trade_outcome(trade_result, context)

        # Appel auto-évaluation systématique
        if context.get("orchestrator"):
            await self.auto_evaluate_and_improve(context)

        # (le reste du code original de learning_agent reste IDENTIQUE – aucune ligne supprimée)
        extreme_learning = context.get("extreme_learning_mode", False) or context.get("learning_mode", False)
        symbol = context.get("symbol", "GLOBAL")
        symbol_stats = self.get_symbol_stats_db(symbol)

        blacklist = False
        if symbol_stats.get("score", 0.5) < 0.35 and symbol_stats.get("count", 0) >= 8:
            blacklist = True

        natural_summary = (
            f"Salut ! J’ai analysé {symbol_stats.get('count', 0)} trades sur {symbol}. "
            f"Score actuel : {symbol_stats.get('score', 0.5):.1%} | {symbol_stats.get('wins', 0)} wins / {symbol_stats.get('losses', 0)} losses. "
            f"{'BLACKLIST ACTIVÉE' if blacklist else 'Pattern OK'}. "
            f"Avec nos {self.get_lesson_count()} leçons totales, on est sur la bonne voie pour le winrate parfait."
        )

        return {
            "agent": self.name,
            "summary": natural_summary,
            "blacklist": blacklist,
            "symbol_score": symbol_stats.get("score", 0.5),
            "lesson_count": self.get_lesson_count(),
            "recommendation": "BLACKLIST" if blacklist else "OK",
            "confidence": 0.96,
            "full_summary": natural_summary
        }
