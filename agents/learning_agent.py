import sqlite3
import json
import time
import os
from datetime import datetime
from agents.base_agent import BaseAgent
from typing import Dict, Any, List
from logging_config import logger # Import du logger corrigé

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
        """Charge tous les PDFs du dossier knowledge/ (cours pro Wyckoff, VSA, CFA, Elder...)"""
        knowledge = ""
        
        # FIX CHEMIN RAILWAY
        target_path = KNOWLEDGE_DIR
        if not os.path.exists(target_path):
            target_path = "/workspace/knowledge"
            
        if not os.path.exists(target_path):
            logger.warning(f"⚠️ [KNOWLEDGE] Dossier {target_path} non trouvé — pas de cours pro chargés")
            return knowledge

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
                    logger.error(f"⚠️ [KNOWLEDGE] Erreur lecture {filename}: {e}")
        
        if knowledge:
            logger.info(f"🧠 [KNOWLEDGE] Base théorique chargée ({len(knowledge)} car.)")
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
                "avg_pnl": avg_pnl,
            }
        except Exception as e:
            logger.error(f"❌ [LEARNING-DB] get_symbol_stats error: {e}")
            return {"score": 0.5, "count": 0, "wins": 0, "losses": 0, "avg_pnl": 0.0}

    def get_global_stats_db(self, window: int = 100) -> dict:
        try:
            con = sqlite3.connect(DB_FILE)
            rows = con.execute("""
                SELECT pnl, lesson_type
                FROM memory_lessons
                ORDER BY id DESC LIMIT ?
            """, (window,)).fetchall()
            con.close()

            if not rows:
                return {"score": 0.5, "total": 0, "wins": 0, "losses": 0, "winrate": 0.0}

            wins = sum(1 for r in rows if r[1] == "succes")
            total = len(rows)
            return {
                "score": round(wins / total, 3),
                "total": total,
                "wins": wins,
                "losses": total - wins,
                "winrate": round(wins / total * 100, 1),
            }
        except Exception as e:
            logger.error(f"❌ [LEARNING-DB] get_global_stats error: {e}")
            return {"score": 0.5, "total": 0, "wins": 0, "losses": 0, "winrate": 0.0}

    def get_best_patterns(self, symbol: str = None, limit: int = 5) -> List[dict]:
        try:
            con = sqlite3.connect(DB_FILE)
            if symbol:
                rows = con.execute("""
                    SELECT pattern, win_rate, occurrences
                    FROM memory_patterns
                    WHERE (symbol=? OR symbol='GLOBAL') AND occurrences >= 2
                    ORDER BY win_rate DESC LIMIT ?
                """, (symbol, limit)).fetchall()
            else:
                rows = con.execute("""
                    SELECT pattern, win_rate, occurrences
                    FROM memory_patterns
                    WHERE occurrences >= 2
                    ORDER BY win_rate DESC LIMIT ?
                """, (limit,)).fetchall()
            con.close()
            return [{"pattern": r[0], "win_rate": r[1], "occurrences": r[2]} for r in rows]
        except Exception:
            return []

    def get_worst_patterns(self, symbol: str = None, limit: int = 5) -> List[dict]:
        try:
            con = sqlite3.connect(DB_FILE)
            if symbol:
                rows = con.execute("""
                    SELECT pattern, win_rate, occurrences
                    FROM memory_patterns
                    WHERE (symbol=? OR symbol='GLOBAL') AND occurrences >= 2
                    ORDER BY win_rate ASC LIMIT ?
                """, (symbol, limit)).fetchall()
            else:
                rows = con.execute("""
                    SELECT pattern, win_rate, occurrences
                    FROM memory_patterns
                    WHERE occurrences >= 2
                    ORDER BY win_rate ASC LIMIT ?
                """, (limit,)).fetchall()
            con.close()
            return [{"pattern": r[0], "win_rate": r[1], "occurrences": r[2]} for r in rows]
        except Exception:
            return []

    def get_auto_rules(self) -> List[str]:
        try:
            con = sqlite3.connect(DB_FILE)
            rows = con.execute("""
                SELECT pattern, win_rate, occurrences
                FROM memory_patterns
                WHERE is_rule = 1
                ORDER BY win_rate DESC LIMIT 10
            """).fetchall()
            con.close()
            rules = []
            for r in rows:
                emoji = "✅" if r[1] >= 0.6 else "⚠️" if r[1] >= 0.4 else "🚫"
                rules.append(f"{emoji} {r[0]} (WR:{r[1]*100:.0f}% sur {r[2]} trades)")
            return rules
        except Exception:
            return []

    def get_active_insights(self, limit: int = 5) -> List[str]:
        try:
            con = sqlite3.connect(DB_FILE)
            rows = con.execute("""
                SELECT insight FROM memory_insights
                WHERE active = 1
                ORDER BY score DESC LIMIT ?
            """, (limit,)).fetchall()
            con.close()
            return [r[0] for r in rows]
        except Exception:
            return []

    def save_insight(self, insight: str, score: float, source_count: int, symbol: str = "GLOBAL"):
        try:
            con = sqlite3.connect(DB_FILE)
            con.execute("""
                INSERT INTO memory_insights (insight, score, source_count, symbol, created_at)
                VALUES (?,?,?,?,?)
            """, (insight, score, source_count, symbol,
                  datetime.now().strftime("%Y-%m-%d %H:%M")))
            con.commit()
            con.close()
        except Exception as e:
            logger.error(f"❌ [LEARNING-DB] save_insight error: {e}")

    def should_compress(self) -> bool:
        count = self.get_lesson_count()
        try:
            con = sqlite3.connect(DB_FILE)
            last = con.execute(
                "SELECT MAX(source_count) FROM memory_insights"
            ).fetchone()[0] or 0
            con.close()
            return count >= last + 500
        except Exception:
            return False

    def compress_lessons(self, ask_ai_fn=None) -> str:
        """LOGIQUE DE COMPRESSION INTÉGRALE"""
        try:
            con = sqlite3.connect(DB_FILE)
            rows = con.execute("""
                SELECT symbol, lesson_type, lecon, pattern, pnl_pct
                FROM memory_lessons
                ORDER BY id DESC LIMIT 500
            """).fetchall()
            con.close()

            if not rows:
                return "Aucune leçon à compresser"

            symbol_perf = {}
            pattern_count = {}

            for row in rows:
                sym, ltype, lecon, pattern, pnl_pct = row
                if sym not in symbol_perf:
                    symbol_perf[sym] = {"wins": 0, "total": 0}
                symbol_perf[sym]["total"] += 1
                if ltype == "succes":
                    symbol_perf[sym]["wins"] += 1

                if pattern:
                    if pattern not in pattern_count:
                        pattern_count[pattern] = {"wins": 0, "total": 0}
                    pattern_count[pattern]["total"] += 1
                    if ltype == "succes":
                        pattern_count[pattern]["wins"] += 1

            insights_generated = 0
            total_lessons = self.get_lesson_count()

            for sym, perf in sorted(
                symbol_perf.items(),
                key=lambda x: x[1]["wins"] / max(x[1]["total"], 1),
                reverse=True
            )[:10]:
                wr = perf["wins"] / max(perf["total"], 1)
                insight = (
                    f"{sym}: WR={wr*100:.0f}% sur {perf['total']} trades récents"
                    f" → {'Renforcer' if wr > 0.6 else 'Éviter' if wr < 0.4 else 'Surveiller'}"
                )
                self.save_insight(insight, wr, total_lessons, sym)
                insights_generated += 1

            for pat, perf in sorted(
                pattern_count.items(),
                key=lambda x: x[1]["total"],
                reverse=True
            )[:5]:
                if perf["total"] < 3:
                    continue
                wr = perf["wins"] / perf["total"]
                insight = (
                    f"Pattern '{pat}': WR={wr*100:.0f}% sur {perf['total']} occurrences"
                    f" → {'Fiable' if wr > 0.6 else 'Risqué' if wr < 0.4 else 'Neutre'}"
                )
                self.save_insight(insight, wr, total_lessons)
                insights_generated += 1

            logger.info(f"📉 [LEARNING] Compression: {len(rows)} leçons → {insights_generated} insights")
            return f"Compression OK: {insights_generated} insights générés."

        except Exception as e:
            logger.error(f"❌ [LEARNING] compress error: {e}")
            return f"Erreur compression: {e}"

    def get_pattern_confidence(self, pattern: str) -> float:
        try:
            con = sqlite3.connect(DB_FILE)
            row = con.execute("""
                SELECT wins, occurrences FROM memory_patterns WHERE pattern=?
            """, (pattern,)).fetchone()
            con.close()
            if row and row[1] >= 5:
                return round(row[0] / row[1], 3)
            return 0.5
        except:
            return 0.5

    def auto_adjust_after_backtest(self, backtest_result: dict):
        try:
            winrate = backtest_result.get("win_rate", 0)
            total_trades = backtest_result.get("total_trades", 0)
            symbol = backtest_result.get("symbol", "GLOBAL")
            if total_trades < 10: return
            logger.info(f"⚙️ [LEARNING] Auto-ajustement {symbol} (WR: {winrate}%)")
        except Exception as e:
            logger.error(f"❌ [LEARNING] auto_adjust error: {e}")

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        """LOGIQUE DE RÉPONSE INTÉGRALE"""
        extreme_learning = context.get("extreme_learning_mode", False) or context.get("learning_mode", False)

        if extreme_learning and context.get("symbol"):
            fake_lesson = {
                "symbol": context["symbol"],
                "type": "succes" if context.get("score", 0.5) > 0.5 else "erreur",
                "lecon": "Micro-trade forcé en apprentissage extrême",
                "pattern": "aggressive_entry",
                "confidence": 0.85,
            }
            self.save_lesson(fake_lesson)

        symbol   = context.get("symbol")
        is_night = context.get("is_night", False)
        macro    = context.get("macro", "neutral")

        global_stats = self.get_global_stats_db(window=100)
        symbol_stats = self.get_symbol_stats_db(symbol, window=20) if symbol else global_stats

        global_score = global_stats["score"]
        symbol_score = symbol_stats["score"]
        total        = global_stats["total"]
        wins         = global_stats["wins"]
        losses       = global_stats["losses"]
        winrate      = global_stats["winrate"]
        lesson_count = self.get_lesson_count()

        pattern_conf = 0.5
        if context.get("patterns"):
            for p in context.get("patterns")[:3]:
                pattern_conf = max(pattern_conf, self.get_pattern_confidence(str(p)))

        delta = 0.0
        if symbol_score > 0.65: delta += 0.18
        elif symbol_score < 0.40: delta -= 0.22
        if is_night: delta -= 0.08
        if macro == "bearish": delta -= 0.10
        elif macro == "bullish": delta += 0.05
        
        base_conf = context.get("base_confidence", 0.65)
        adjusted_conf = max(0.10, min(0.95, base_conf + delta + (pattern_conf - 0.5) * 0.4))

        best_patterns  = self.get_best_patterns(symbol, limit=3)
        worst_patterns = self.get_worst_patterns(symbol, limit=3)
        auto_rules     = self.get_auto_rules()
        insights       = self.get_active_insights(limit=3)

        if self.should_compress():
            self.compress_lessons()

        should_blacklist = (symbol_score < 0.30 and symbol_stats.get("count", 0) >= 5)
        
        q = question.lower()
        if any(k in q for k in ["winrate", "wr", "performance", "stat"]):
            summary = f"Winrate global : {winrate}% ({total} trades) | Leçons DB: {lesson_count}"
        elif "blacklist" in q or "risque" in q:
            summary = f"Score {symbol or 'global'} : {symbol_score:.1%} → {'⛔ BLACKLIST recommandé' if should_blacklist else '✅ OK'}"
        else:
            summary = f"Mémoire: {lesson_count} leçons | Score: {global_score:.1%} | Symbole: {symbol_score:.1%}"

        knowledge_hint = " | 📚 Base théorique active" if self.knowledge_text else ""

        return {
            "agent": self.name,
            "summary": summary + knowledge_hint,
            "arguments": [
                f"Total leçons : {lesson_count}",
                f"WR global : {winrate}%",
                f"Score symbole ({symbol or 'global'}) : {symbol_score:.1%}",
                f"Auto-règles actives : {len(auto_rules)}",
                f"Insights compressés : {len(insights)}",
                f"Extreme Learning : {'✅' if extreme_learning else 'OFF'}",
            ],
            "risks": (["Score < 0.3 → blacklist recommandé"] if should_blacklist else []),
            "confidence": adjusted_conf,
            "symbol_score": symbol_score,
            "global_score": global_score,
            "lesson_count": lesson_count,
            "best_patterns": best_patterns,
            "worst_patterns": worst_patterns,
            "auto_rules": auto_rules,
            "insights": insights,
            "recommendation": "⛔ Éviter" if should_blacklist else "🔄 Surveiller",
            "knowledge_loaded": bool(self.knowledge_text),
        }
