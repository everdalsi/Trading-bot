"""
👑 TRADER AGENT V3 — Décision enrichie par la mémoire infinie
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Améliorations vs V2 :

- N’instancie plus LearningAgent en interne (couplage fort supprimé)
- Utilise directement le symbol_score injecté dans le contexte
- Intègre les auto_rules et best_patterns dans la décision
- Anti-overtrading renforcé
- Confiance basée sur le score composite (learning + global + macro)
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

        # ─── Veto 1 : Dégradation de performance ───
        if degraded:
            return self._hold(symbol, "Performance dégradée — pause prudente",
                              symbol_score, global_score)

        # ─── Veto 2 : Série de pertes ───
        if streak_type == "loss" and streak_count >= 5:
            return self._hold(
                symbol,
                f"Série de {streak_count} pertes consécutives — attendre",
                symbol_score, global_score
            )

        # ─── Veto 3 : Score symbole trop faible ───
        if symbol_score < 0.25 and lesson_count >= 10:
            return self._hold(
                symbol,
                f"Score symbole insuffisant ({symbol_score:.1%}) — éviter",
                symbol_score, global_score
            )

        # ─── Veto 4 : Pattern blacklisté détecté ───
        current_patterns = context.get("patterns", [])
        bad_pattern_names = {p.get("pattern", "") for p in worst_patterns
                             if p.get("win_rate", 1.0) < 0.30}
        for pat in current_patterns:
            pat_name = pat.get("name", "") if isinstance(pat, dict) else str(pat)
            if pat_name in bad_pattern_names:
                return self._hold(
                    symbol,
                    f"Pattern blacklisté détecté : {pat_name}",
                    symbol_score, global_score
                )

        # ─── Veto 5 : Anti-overtrading ───
        memory = context.get("memory", {})
        recent_trades = (memory.get("trades", []) or [])[-8:]
        same_symbol = [t for t in recent_trades if t.get("symbol") == symbol]
        if len(same_symbol) >= 3:
            return self._hold(
                symbol,
                "Anti-overtrading : trop de trades récents sur ce symbole",
                symbol_score, global_score
            )

        # ─── Décision principale ───
        decision = "HOLD"
        reason   = "Pas de signal clair"

        # Score composite
        composite = (symbol_score * 0.6 + global_score * 0.4)

        # Conditions BUY
        if (macro in ("bullish", "BULL")
                and composite >= 0.45) or composite >= 0.52:
                and "CRITICAL" not in str(risk.get("summary", ""))
                and "STOP" not in str(risk.get("recommendation", ""))):
            decision = "BUY"
            reason   = f"Macro haussier + composite={composite:.2f} + risque OK"

        elif composite >= 0.68:
            decision = "BUY"
            reason   = f"Score composite très fort ({composite:.2f})"

        # Conditions SELL
        elif (macro in ("bearish", "BEAR") and composite <= 0.38):
            decision = "SELL"
            reason   = "Macro baissier + score faible"

        # Boost via auto-règles
        if decision == "HOLD" and auto_rules:
            buy_rules = [r for r in auto_rules if "✅" in r]
            if len(buy_rules) >= 2 and composite >= 0.52:
                decision = "BUY"
                reason   = f"Règles automatiques actives ({len(buy_rules)}) + score {composite:.2f}"

        # Boost si meilleur pattern connu détecté
        if decision == "HOLD" and best_patterns:
            top_pattern_names = {p.get("pattern", "") for p in best_patterns
                                 if p.get("win_rate", 0) >= 0.65}
            for pat in current_patterns:
                pat_name = pat.get("name", "") if isinstance(pat, dict) else str(pat)
                if pat_name in top_pattern_names and composite >= 0.50:
                    decision = "BUY"
                    reason   = f"Meilleur pattern reconnu : {pat_name}"
                    break

        # Confiance finale
        if decision == "HOLD":
            confidence = round(max(0.20, composite * 0.6), 2)
        else:
            confidence = round(max(0.40, min(0.95, composite * 1.1)), 2)

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
        """Retourne une réponse HOLD formatée."""
        return {
            "agent":        self.name,
            "symbol":       symbol,
            "decision":     "HOLD",
            "confidence":   0.20,
            "symbol_score": round(symbol_score, 2),
            "global_score": round(global_score, 2),
            "composite":    round((symbol_score * 0.6 + global_score * 0.4), 2),
            "summary":      f"{symbol} → HOLD | {reason[:80]}",
            "reason":       reason,
            "macro":        "neutral",
        }
