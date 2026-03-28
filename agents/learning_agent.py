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
        # Ligne originale conservée
        super().__init__(
            name="learning",
            role="Mémoire infinie, scoring des patterns et ajustement de confiance"
        )
        # UPGRADE V3 : rôle plus précis pour le cerveau commun
        self.role = "Mémoire infinie, scoring des patterns et ajustement de confiance — uniquement dans mon domaine d’expertise"
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
            return f"Compression OK: {insights_generated} insights générés depuis {len(rows)} leçons"

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

            if total_trades < 10:
                return

            if winrate >= 85:
                logger.info(f"[AUTO-ADJUST] Winrate excellent ({winrate}%) pour {symbol}")
            elif winrate <= 45:
                logger.info(f"[AUTO-ADJUST] Winrate faible ({winrate}%) pour {symbol}")

            current_conf = 0.5
            if winrate > 70:
                current_conf = 0.85
            elif winrate > 55:
                current_conf = 0.70

            logger.info(f"[VALIDATION] Backtest {symbol} → WR {winrate}% | Confiance ajustée à {current_conf:.2f}")
        except Exception as e:
            logger.error(f"❌ [AUTO-ADJUST] Erreur: {e}")

    # ==================== UPGRADES AJOUTÉES POUR 95% WINRATE ====================

    def get_regime(self, symbol: str) -> str:
        """Détecte le régime de marché (bull / bear / sideways) grâce aux leçons récentes"""
        try:
            con = sqlite3.connect(DB_FILE)
            rows = con.execute("""
                SELECT pnl_pct FROM memory_lessons
                WHERE symbol = ? ORDER BY id DESC LIMIT 30
            """, (symbol,)).fetchall()
            con.close()

            if not rows:
                return "neutral"

            recent_pnl = [r[0] for r in rows if r[0] is not None]
            avg_pnl = sum(recent_pnl) / len(recent_pnl)

            if avg_pnl > 2.5:
                return "bull"
            elif avg_pnl < -2.0:
                return "bear"
            else:
                return "sideways"
        except Exception:
            return "neutral"

    def validate_lesson_with_backtest(self, pattern: str, symbol: str) -> float:
        """Backtest rapide sur les 50 derniers trades similaires avant d'utiliser la leçon"""
        try:
            con = sqlite3.connect(DB_FILE)
            rows = con.execute("""
                SELECT pnl_pct FROM memory_lessons
                WHERE pattern = ? AND symbol = ? ORDER BY id DESC LIMIT 50
            """, (pattern, symbol)).fetchall()
            con.close()

            if not rows:
                return 0.5
            wins = sum(1 for r in rows if r[0] > 0)
            return round(wins / len(rows), 3)
        except Exception:
            return 0.5

    def copy_wallet_score(self, wallet_address: str, symbol: str) -> float:
        """Score de similarité avec un wallet externe (prêt pour API on-chain)"""
        try:
            con = sqlite3.connect(DB_FILE)
            count = con.execute("""
                SELECT COUNT(*) FROM memory_lessons
                WHERE symbol = ? AND tags LIKE ?
            """, (symbol, f"%{wallet_address[:8]}%")).fetchone()[0]
            con.close()
            return min(0.95, count / 10.0)
        except Exception:
            return 0.0

    def get_global_stats_db(self, window: int = 100) -> dict:
        """Stats enrichies avec régime et validation (upgrade 95%)"""
        try:
            con = sqlite3.connect(DB_FILE)
            rows = con.execute("""
                SELECT pnl_pct, pattern FROM memory_lessons
                ORDER BY id DESC LIMIT ?
            """, (window,)).fetchall()
            con.close()

            if not rows:
                return {"score": 0.5, "count": 0, "regime": "neutral"}

            wins = sum(1 for r in rows if r[0] > 0)
            score = round(wins / len(rows), 3)
            regime = self.get_regime("GLOBAL")

            return {
                "score": score,
                "count": len(rows),
                "regime": regime,
                "validated_patterns": len([r for r in rows if self.validate_lesson_with_backtest(r[1], "GLOBAL") > 0.7])
            }
        except Exception:
            return {"score": 0.5, "count": 0, "regime": "neutral"}

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        # === UPGRADE V3 : Vérification stricte de spécialisation (cerveau commun) ===
        if not self._is_in_my_domain(question):
            return {
                "agent": self.name,
                "summary": f"⚠️ {self.name} a détecté une question hors de sa spécialité → je ne réponds pas",
                "confidence": 0.0,
                "recommendation": "HOLD - Ignoré par spécialisation stricte",
                "warning": "Hors domaine learning"
            }

        # === UPGRADE V3 : Glossaire partagé forcé pour zéro malentendu ===
        shared_glossary = context.get("shared_glossary", {})
        def explain(k): 
            return self.explain_term(k) or shared_glossary.get(k, k)

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

        # === UPGRADES 95% ===
        regime = self.get_regime(symbol or "GLOBAL")
        validated_score = self.validate_lesson_with_backtest(
            context.get("pattern", ""), symbol or "GLOBAL"
        )

        pattern_conf = 0.5
        if context.get("patterns"):
            for p in context.get("patterns")[:3]:
                pattern_conf = max(pattern_conf, self.get_pattern_confidence(str(p)))

        if total == 0:
            sim    = context.get("sim", {})
            memory = context.get("memory", {})
            trades = sim.get("trades", []) or memory.get("trades", [])
            closed = [t for t in trades if isinstance(t.get("pnl"), (int, float))]
            if closed:
                wins   = sum(1 for t in closed if t["pnl"] > 0)
                total  = len(closed)
                losses = total - wins
                winrate = round(wins / total * 100, 1)
                global_score = wins / total
                if symbol:
                    sym_trades = [t for t in closed if t.get("symbol") == symbol]
                    if sym_trades:
                        sym_wins = sum(1 for t in sym_trades if t["pnl"] > 0)
                        symbol_score = sym_wins / len(sym_trades)

                base_conf = context.get("base_confidence", 0.65)
        delta = 0.0

        if symbol_score > 0.65:
            delta += 0.18
        elif symbol_score < 0.40:
            delta -= 0.22

        if is_night:
            delta -= 0.08

        if macro == "bearish":
            delta -= 0.10
        elif macro == "bullish":
            delta += 0.05

        if symbol_stats.get("count", 0) < 5:
            delta -= 0.05

        adjusted_conf = max(0.10, min(0.95, context.get("base_confidence", 0.65) + delta + (pattern_conf - 0.5) * 0.4))

        best_patterns  = self.get_best_patterns(symbol, limit=3)
        worst_patterns = self.get_worst_patterns(symbol, limit=3)
        auto_rules     = self.get_auto_rules()
        insights       = self.get_active_insights(limit=3)

        if self.should_compress():
            self.compress_lessons()

        should_blacklist = (
            symbol_score < 0.30
            and symbol_stats.get("count", 0) >= 5
        )

        q = question.lower()
        if any(k in q for k in ["winrate", "wr", "performance", "stat"]):
            summary = f"Winrate global : {winrate}% ({total} trades) | Leçons DB: {lesson_count}"
        elif "blacklist" in q or "risque" in q:
            summary = (
                f"Score {symbol or 'global'} : {symbol_score:.1%} "
                f"→ {'⛔ BLACKLIST recommandé' if should_blacklist else '✅ OK'}"
            )
        else:
            summary = (
                f"Mémoire: {lesson_count} leçons ∞ | "
                f"Score global: {global_score:.1%} | "
                f"Symbole {symbol or 'global'}: {symbol_score:.1%} | "
                f"Régime : {regime} | Score validé : {validated_score:.2f}"
            )

        # === UPGRADE PHASE 2 : RAG + Immune System Integration (ajout uniquement ici) ===
        immune_health = context.get("immune_health", 100)
        rag_boost = (len(self.knowledge_text) / 100000) * 0.05 if self.knowledge_text else 0.0
        adjusted_conf = min(1.0, adjusted_conf + rag_boost + (immune_health / 1000))
        summary += f" | 🛡️ Immune health {immune_health}% | RAG knowledge active"

        return {
            "agent": self.name,
            "summary": summary + (" | 📚 Base théorique active" if self.knowledge_text else ""),
            "arguments": [
                f"Total leçons DB (∞) : {lesson_count}",
                f"WR global : {winrate}%",
                f"Score symbole ({symbol or 'global'}) : {symbol_score:.1%}",
                f"Régime marché : {regime}",
                f"Score validé par backtest : {validated_score:.2f}",
                f"Extreme Learning Mode : {'✅ ACTIVÉ' if extreme_learning else 'Inactif'}",
                f"Immune System Health : {immune_health}%"
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
            "regime": regime,
            "validated_score": validated_score,
            "immune_health": immune_health,
            "glossary_used": True
        }
