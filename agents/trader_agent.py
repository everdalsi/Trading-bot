"""
👑 TRADER AGENT V4 — Stratégie optimisée + utilisation maximale de la mémoire infinie
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Améliorations vs V3 :
- Score composite plus intelligent (symbol 60% + global 30% + pattern bonus 10%)
- Meilleurs patterns boostent fortement la confiance
- Worst patterns = veto automatique
- Auto-rules utilisées de manière plus agressive
- Meilleure gestion des streaks et dégradation
- Confiance dynamique selon le nombre de leçons
"""

from agents.base_agent import BaseAgent
from typing import Dict, Any


class TraderAgent(BaseAgent):

    def __init__(self):
        super().__init__(
            name="trader",
            role="Décision trading (BUY / SELL / HOLD)"
        )

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        # === EXTREME LEARNING MODE (MAX TRADES) ===
        extreme_learning = context.get("extreme_learning_mode", False) or context.get("learning_mode", False)

        if extreme_learning:
            composite = (context.get("symbol_score", 0.5) * 0.6 + 
                        context.get("global_score", 0.5) * 0.3 + 
                        0.1)  # bonus pour forcer l'apprentissage
            return {
                "agent": self.name,
                "decision": "BUY",
                "confidence": round(min(0.95, composite * 1.3), 2),
                "summary": f"{context.get('symbol')} → BUY (apprentissage extrême optimisé) | composite={composite:.2f}",
                "reason": "Mode apprentissage extrême → volume prioritaire + patterns",
                "composite": composite,
            }

        macro          = context.get("macro", "neutral")
        symbol         = context.get("symbol", "UNKNOWN")
        analysis       = context.get("analysis", {})
        risk           = context.get("risk", {})
        global_score   = context.get("global_score", context.get("score", 0.5))
        symbol_score   = context.get("symbol_score", global_score)
        lesson_count   = context.get("lesson_count", 0)
        auto_rules     = context.get("auto_rules", [])
        best_patterns  = context.get("best_patterns", [])
        worst_patterns = context.get("worst_patterns", [])
        degraded       = context.get("degraded", False)
        streak_type    = context.get("streak_type", "neutral")
        streak_count   = context.get("streak_count", 0)

        # ─── VETO 1 : Dégradation de performance ───
        if degraded:
            return self._hold(symbol, "Performance dégradée — pause prudente",
                              symbol_score, global_score)

        # ─── VETO 2 : Série de pertes ───
        if streak_type == "loss" and streak_count >= 5:
            return self._hold(
                symbol,
                f"Série de {streak_count} pertes consécutives — attendre",
                symbol_score, global_score
            )

        # ─── VETO 3 : Score symbole trop faible ───
        if symbol_score < 0.28 and lesson_count >= 20:
            return self._hold(
                symbol,
                f"Score symbole insuffisant ({symbol_score:.1%}) — éviter",
                symbol_score, global_score
            )

        # ─── VETO 4 : Pattern blacklisté détecté ───
        current_patterns = context.get("patterns", [])
        bad_pattern_names = {p.get("pattern", "") for p in worst_patterns
                             if p.get("win_rate", 1.0) < 0.35}
        for pat in current_patterns:
            pat_name = pat.get("name", "") if isinstance(pat, dict) else str(pat)
            if pat_name in bad_pattern_names:
                return self._hold(
                    symbol,
                    f"Pattern blacklisté détecté : {pat_name}",
                    symbol_score, global_score
                )

        # ─── VETO 5 : Anti-overtrading renforcé ───
        memory = context.get("memory", {})
        recent_trades = (memory.get("trades", []) or [])[-15:]
        same_symbol = [t for t in recent_trades if t.get("symbol") == symbol]

        severe_losses = sum(1 for t in same_symbol if t.get("pnl_pct", 0) <= -90)
        if len(same_symbol) >= 4 or severe_losses >= 2:
            return self._hold(
                symbol,
                f"Anti-spam : {len(same_symbol)} trades récents dont {severe_losses} SL sévères sur {symbol}",
                symbol_score, global_score
            )

        # ─── Décision principale (optimisée) ───
        decision = "HOLD"
        reason   = "Pas de signal clair"

        composite = (symbol_score * 0.65 + global_score * 0.25)

        # Bonus patterns très fort en mode précision
        if best_patterns:
            top_patterns = [p for p in best_patterns if p.get("win_rate", 0) >= 0.75]
            if top_patterns:
                composite += 0.18
            elif any(p.get("win_rate", 0) >= 0.65 for p in best_patterns):
                composite += 0.12

        # BUY plus intelligent et sélectif
        if (macro in ("bullish", "BULL") and composite >= 0.78) or composite >= 0.82:
            if ("CRITICAL" not in str(risk.get("summary", "")) and
                "STOP" not in str(risk.get("recommendation", ""))):
                decision = "BUY"
                reason   = f"Macro haussier + composite optimisé={composite:.2f} + risque OK"

        elif composite >= 0.78:
            decision = "BUY"
            reason   = f"Score composite très fort ({composite:.2f})"

        # Boost via auto-règles
        if decision == "HOLD" and auto_rules:
            buy_rules = [r for r in auto_rules if "✅" in r]
            if len(buy_rules) >= 2 and composite >= 0.65:
                decision = "BUY"
                reason   = f"Règles automatiques actives ({len(buy_rules)}) + score {composite:.2f}"

        # Confiance finale dynamique
        if decision == "HOLD":
            confidence = round(max(0.20, composite * 0.65), 2)
        else:
            confidence = round(max(0.55, min(0.96, composite * 1.25)), 2)

        return {
            "agent":        self.name,
            "symbol":       symbol,
            "decision":     decision,
            "confidence":   confidence,
            "symbol_score": round(symbol_score, 2),
            "global_score": round(global_score, 2),
            "composite":    round(composite, 2),
            "summary":      (
                f"{symbol} → {decision} | "
                f"composite={composite:.2f} | conf={confidence:.2f} | "
                f"leçons={lesson_count}∞"
            ),
            "reason": reason,
            "macro":  macro,
        }

    def _hold(self, symbol: str, reason: str,
              symbol_score: float, global_score: float) -> Dict[str, Any]:
        return {
            "agent":        self.name,
            "symbol":       symbol,
            "decision":     "HOLD",
            "confidence":   0.20,
            "symbol_score": round(symbol_score, 2),
            "global_score": round(global_score, 2),
            "composite":    round((symbol_score * 0.65 + global_score * 0.25), 2),
            "summary":      f"{symbol} → HOLD | {reason[:80]}",
            "reason":       reason,
            "macro":        "neutral",
        }
