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
            # Table des leçons
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
            # Table des insights
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
            # Table des patterns
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
            con.execute("CREATE INDEX IF NOT EXISTS idx_lessons_symbol ON memory_lessons(symbol)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_lessons_type ON memory_lessons(lesson_type)")
            con.commit()
            con.close()
        except Exception as e:
            logger.error(f"❌ [LEARNING-DB] Erreur initialisation SQL : {e}")

    def _load_knowledge_base(self) -> str:
        """Charge le texte des PDFs. Détecte le dossier 'knowledge' ou la racine '/' """
        knowledge = ""
        
        # Test de plusieurs chemins pour Railway
        paths_to_try = [
            os.path.join(os.getcwd(), "knowledge"),
            "/workspace/knowledge",
            os.getcwd() # Racine du bot (là où sont tes PDFs d'après tes captures)
        ]
        
        target_path = None
        for p in paths_to_try:
            if os.path.exists(p):
                # On vérifie s'il y a des PDFs à l'intérieur
                pdfs = [f for f in os.listdir(p) if f.lower().endswith(".pdf")]
                if pdfs:
                    target_path = p
                    break
        
        if not target_path:
            logger.warning("⚠️ [KNOWLEDGE] Aucun PDF théorique trouvé (ni dans /knowledge ni à la racine)")
            return ""

        logger.info(f"📖 [KNOWLEDGE] Chargement des cours depuis : {target_path}")
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
                    knowledge += f"\n\n--- SOURCE: {filename.upper()} ---\n{text}\n"
                    logger.info(f"✅ [KNOWLEDGE] '{filename}' ajouté au cerveau de l'agent.")
                except Exception as e:
                    logger.error(f"❌ [KNOWLEDGE] Erreur lecture {filename}: {e}")
        
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
            logger.error(f"❌ [LEARNING-DB] Erreur save_lesson : {e}")
            return -1

    def _update_pattern(self, pattern: str, symbol: str, is_win: bool):
        if not pattern or pattern == "aucun_pattern":
            return
        try:
            con = sqlite3.connect(DB_FILE)
            row = con.execute("SELECT id, occurrences, wins, losses FROM memory_patterns WHERE pattern=?", (pattern,)).fetchone()

            if row:
                occ  = row[1] + 1
                wins = row[2] + (1 if is_win else 0)
                loss = row[3] + (0 if is_win else 1)
                wr   = wins / occ
                is_rule = 1 if occ >= 3 else 0
                con.execute("""
                    UPDATE memory_patterns
                    SET occurrences=?, wins=?, losses=?, win_rate=?, last_seen=?, is_rule=?
                    WHERE id=?
                """, (occ, wins, loss, wr, datetime.now().strftime("%Y-%m-%d %H:%M"), is_rule, row[0]))
            else:
                con.execute("""
                    INSERT INTO memory_patterns (pattern, symbol, occurrences, wins, losses, win_rate, last_seen)
                    VALUES (?,?,1,?,?,?,?)
                """, (pattern, symbol, 1 if is_win else 0, 0 if is_win else 1, 1.0 if is_win else 0.0, datetime.now().strftime("%Y-%m-%d %H:%M")))
            con.commit()
            con.close()
        except Exception as e:
            logger.error(f"❌ [LEARNING-DB] Erreur update_pattern : {e}")

    def get_lesson_count(self) -> int:
        try:
            con = sqlite3.connect(DB_FILE)
            count = con.execute("SELECT COUNT(*) FROM memory_lessons").fetchone()[0]
            con.close()
            return count
        except: return 0

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
            if not rows: return {"score": 0.5, "count": 0, "wins": 0, "losses": 0, "avg_pnl": 0.0}
            wins = sum(1 for r in rows if r[2] == "succes")
            avg_pnl = round(sum(r[0] for r in rows if r[0] is not None) / len(rows), 4)
            return {"score": round(wins / len(rows), 3), "count": len(rows), "wins": wins, "losses": len(rows)-wins, "avg_pnl": avg_pnl}
        except: return {"score": 0.5, "count": 0}

    def get_global_stats_db(self, window: int = 100) -> dict:
        try:
            con = sqlite3.connect(DB_FILE)
            rows = con.execute("SELECT pnl, lesson_type FROM memory_lessons ORDER BY id DESC LIMIT ?", (window,)).fetchall()
            con.close()
            if not rows: return {"score": 0.5, "total": 0, "wins": 0, "losses": 0, "winrate": 0.0}
            wins = sum(1 for r in rows if r[1] == "succes")
            return {"score": round(wins / len(rows), 3), "total": len(rows), "winrate": round(wins / len(rows) * 100, 1)}
        except: return {"score": 0.5, "total": 0}

    def get_best_patterns(self, symbol: str = None, limit: int = 5) -> List[dict]:
        try:
            con = sqlite3.connect(DB_FILE)
            query = "SELECT pattern, win_rate, occurrences FROM memory_patterns WHERE occurrences >= 2"
            if symbol: query += f" AND (symbol='{symbol}' OR symbol='GLOBAL')"
            query += " ORDER BY win_rate DESC LIMIT ?"
            rows = con.execute(query, (limit,)).fetchall()
            con.close()
            return [{"pattern": r[0], "win_rate": r[1], "occurrences": r[2]} for r in rows]
        except: return []

    def get_worst_patterns(self, symbol: str = None, limit: int = 5) -> List[dict]:
        try:
            con = sqlite3.connect(DB_FILE)
            query = "SELECT pattern, win_rate, occurrences FROM memory_patterns WHERE occurrences >= 2"
            if symbol: query += f" AND (symbol='{symbol}' OR symbol='GLOBAL')"
            query += " ORDER BY win_rate ASC LIMIT ?"
            rows = con.execute(query, (limit,)).fetchall()
            con.close()
            return [{"pattern": r[0], "win_rate": r[1], "occurrences": r[2]} for r in rows]
        except: return []

    def get_auto_rules(self) -> List[str]:
        try:
            con = sqlite3.connect(DB_FILE)
            rows = con.execute("SELECT pattern, win_rate, occurrences FROM memory_patterns WHERE is_rule = 1 ORDER BY win_rate DESC LIMIT 10").fetchall()
            con.close()
            return [f"{'✅' if r[1]>=0.6 else '🚫'} {r[0]} (WR:{r[1]*100:.0f}%)" for r in rows]
        except: return []

    def get_active_insights(self, limit: int = 5) -> List[str]:
        try:
            con = sqlite3.connect(DB_FILE)
            rows = con.execute("SELECT insight FROM memory_insights WHERE active = 1 ORDER BY score DESC LIMIT ?", (limit,)).fetchall()
            con.close()
            return [r[0] for r in rows]
        except: return []

    def save_insight(self, insight: str, score: float, source_count: int, symbol: str = "GLOBAL"):
        try:
            con = sqlite3.connect(DB_FILE)
            con.execute("INSERT INTO memory_insights (insight, score, source_count, symbol, created_at) VALUES (?,?,?,?,?)",
                        (insight, score, source_count, symbol, datetime.now().strftime("%Y-%m-%d %H:%M")))
            con.commit(); con.close()
        except Exception as e:
            logger.error(f"❌ [LEARNING] Erreur save_insight : {e}")

    def should_compress(self) -> bool:
        count = self.get_lesson_count()
        try:
            con = sqlite3.connect(DB_FILE)
            last = con.execute("SELECT MAX(source_count) FROM memory_insights").fetchone()[0] or 0
            con.close()
            return count >= last + 500
        except: return False

    def compress_lessons(self, ask_ai_fn=None) -> str:
        """LOGIQUE DE COMPRESSION INTÉGRALE - Analyse les 500 derniers trades"""
        try:
            con = sqlite3.connect(DB_FILE)
            rows = con.execute("SELECT symbol, lesson_type, lecon, pattern, pnl_pct FROM memory_lessons ORDER BY id DESC LIMIT 500").fetchall()
            con.close()

            if not rows: return "Rien à compresser."

            symbol_perf = {}
            pattern_count = {}

            for row in rows:
                sym, ltype, lecon, pattern, pnl_pct = row
                if sym not in symbol_perf: symbol_perf[sym] = {"wins": 0, "total": 0}
                symbol_perf[sym]["total"] += 1
                if ltype == "succes": symbol_perf[sym]["wins"] += 1

                if pattern and pattern != "aucun_pattern":
                    if pattern not in pattern_count: pattern_count[pattern] = {"wins": 0, "total": 0}
                    pattern_count[pattern]["total"] += 1
                    if ltype == "succes": pattern_count[pattern]["wins"] += 1

            insights_generated = 0
            total_lessons = self.get_lesson_count()

            # Génération d'insights par symbole
            for sym, perf in sorted(symbol_perf.items(), key=lambda x: x[1]["wins"]/max(x[1]["total"],1), reverse=True)[:10]:
                wr = perf["wins"] / max(perf["total"], 1)
                insight = f"{sym}: WR={wr*100:.0f}% sur {perf['total']} trades récents → {'Renforcer' if wr > 0.6 else 'Méfiance' if wr < 0.4 else 'Stable'}"
                self.save_insight(insight, wr, total_lessons, sym)
                insights_generated += 1

            logger.info(f"📉 [LEARNING] Compression terminée : {insights_generated} insights générés.")
            return f"OK: {insights_generated} insights."
        except Exception as e:
            logger.error(f"❌ [LEARNING] Erreur compression : {e}")
            return str(e)

    def get_pattern_confidence(self, pattern: str) -> float:
        try:
            con = sqlite3.connect(DB_FILE)
            row = con.execute("SELECT wins, occurrences FROM memory_patterns WHERE pattern=?", (pattern,)).fetchone()
            con.close()
            if row and row[1] >= 5:
                return round(row[0] / row[1], 3)
            return 0.5
        except: return 0.5

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        """LOGIQUE DE RÉPONSE INTÉGRALE AVEC SCORE COMPLEXE (DELTA RSI/MACRO)"""
        extreme_learning = context.get("extreme_learning_mode", False) or context.get("learning_mode", False)
        
        symbol   = context.get("symbol")
        is_night = context.get("is_night", False)
        macro    = context.get("macro", "neutral")

        global_stats = self.get_global_stats_db(window=100)
        symbol_stats = self.get_symbol_stats_db(symbol, window=20) if symbol else global_stats

        global_score = global_stats["score"]
        symbol_score = symbol_stats["score"]
        lesson_count = self.get_lesson_count()

        # Calcul de la confiance pattern
        pattern_conf = 0.5
        if context.get("patterns"):
            for p in context.get("patterns")[:3]:
                pattern_conf = max(pattern_conf, self.get_pattern_confidence(str(p)))

        # Logique de calcul du Delta (Ajustement fin de la confiance)
        delta = 0.0
        if symbol_score > 0.65: delta += 0.18
        elif symbol_score < 0.40: delta -= 0.22
        if is_night: delta -= 0.08
        if macro == "bearish": delta -= 0.10
        elif macro == "bullish": delta += 0.05
        
        base_conf = context.get("base_confidence", 0.65)
        adjusted_conf = max(0.10, min(0.95, base_conf + delta + (pattern_conf - 0.5) * 0.4))

        if self.should_compress():
            self.compress_lessons()

        should_blacklist = (symbol_score < 0.30 and symbol_stats.get("count", 0) >= 5)
        
        summary = f"WR Global: {global_stats['winrate']}% | Leçons: {lesson_count}"
        if self.knowledge_text: summary += " | 📚 Base théorique ACTIVE"

        return {
            "agent": self.name,
            "summary": summary,
            "arguments": [
                f"Total leçons : {lesson_count}",
                f"WR global : {global_stats['winrate']}%",
                f"Score symbole ({symbol or 'global'}) : {symbol_score:.1%}",
                f"Auto-règles : {len(self.get_auto_rules())}",
                f"Extreme Learning : {'OUI' if extreme_learning else 'NON'}"
            ],
            "confidence": adjusted_conf,
            "symbol_score": symbol_score,
            "global_score": global_score,
            "lesson_count": lesson_count,
            "best_patterns": self.get_best_patterns(symbol),
            "worst_patterns": self.get_worst_patterns(symbol),
            "auto_rules": self.get_auto_rules(),
            "insights": self.get_active_insights(),
            "recommendation": "⛔ BLACKLIST recommandé" if should_blacklist else "✅ OK",
            "knowledge_loaded": bool(self.knowledge_text)
        }
