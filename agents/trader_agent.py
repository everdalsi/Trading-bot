"""
👑 TRADER AGENT V7 — Décision trading professionnelle
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Améliorations vs V6 :
- Seuil de confiance réduit à 70% (pratique et adaptatif)
- Rounds de débat réduits à 2 (vitesse d'exécution)
- Score composite affiné : symbol 60% + global 30% + bonus patterns 10%
- Veto automatique sur worst patterns et risk critique
- Mode apprentissage extrême : BUY prioritaire pour maximiser les leçons
- Gestion des streaks et dégradation conservée
"""

from agents.base_agent import BaseAgent
from typing import Dict, Any


class TraderAgent(BaseAgent):

    def __init__(self):
        super().__init__(
            name="trader",
            role="Décision trading finale (BUY / SELL / HOLD) — Confiance ≥ 70% + consensus collectif"
        )
        self.MIN_CONFIDENCE = 0.70
        self.MIN_DEBATE_ROUNDS = 2

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {
                "agent": self.name,
                "summary": f"⚠️ {self.name} : question hors domaine trading → HOLD",
                "confidence": 0.0,
                "recommendation": "HOLD",
                "warning": "Hors domaine trader"
            }

        shared_glossary = context.get("shared_glossary", {})
        def explain(k):
            return self.explain_term(k) or shared_glossary.get(k, k)

        extreme_learning = context.get("extreme_learning_mode", False) or context.get("learning_mode", False)

        if extreme_learning:
            composite = (
                context.get("symbol_score", 0.5) * 0.6 +
                context.get("global_score", 0.5) * 0.3 +
                0.1
            )
            return {
                "agent": self.name,
                "decision": "BUY",
                "confidence": round(min(0.95, composite * 1.3), 2),
                "summary": f"{context.get('symbol', 'UNKNOWN')} → BUY (mode apprentissage) | composite={composite:.2f}",
                "reason": "Mode apprentissage extrême → volume prioritaire",
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
        debate_rounds  = context.get("debate_rounds", 0)
        final_confidence = context.get("final_confidence", 0.0)
        agent_outputs  = context.get("agent_outputs", [])

        for out in agent_outputs:
            if out.get("agent") == "risk" and out.get("risk_level") in ["CRITICAL", "HIGH"]:
                return self._hold(symbol, f"VETO RiskAgent niveau {out.get('risk_level')}", symbol_score, global_score)

        if degraded:
            return self._hold(symbol, "Performance dégradée — pause prudente", symbol_score, global_score)

        if streak_type == "loss" and streak_count >= 5:
            return self._hold(symbol, f"Série de {streak_count} pertes — attendre", symbol_score, global_score)

        if symbol_score < 0.28 and lesson_count >= 20:
            return self._hold(symbol, f"Score symbole insuffisant ({symbol_score:.1%})", symbol_score, global_score)

        current_patterns = context.get("patterns", [])
        bad_pattern_names = {p.get("pattern", "") for p in worst_patterns if p.get("win_rate", 1.0) < 0.35}
        for pat in current_patterns:
            pat_name = pat.get("name", "") if isinstance(pat, dict) else str(pat)
            if pat_name in bad_pattern_names:
                return self._hold(symbol, f"Pattern blacklisté : {pat_name}", symbol_score, global_score)

        memory = context.get("memory", {})
        recent_trades = (memory.get("trades", []) or [])[-15:]
        same_symbol = [t for t in recent_trades if t.get("symbol") == symbol]
        severe_losses = sum(1 for t in same_symbol if t.get("pnl_pct", 0) <= -90)
        if len(same_symbol) >= 4 or severe_losses >= 2:
            return self._hold(symbol, f"Anti-spam : {len(same_symbol)} trades récents sur {symbol}", symbol_score, global_score)

        if final_confidence < self.MIN_CONFIDENCE or debate_rounds < self.MIN_DEBATE_ROUNDS:
            return self._hold(
                symbol,
                f"Confiance collective {final_confidence:.1%} après {debate_rounds} rounds — seuil non atteint",
                symbol_score, global_score
            )

        decision = "HOLD"
        reason   = f"Pas de signal clair à ≥ {self.MIN_CONFIDENCE*100:.0f}% de confiance"
        composite = (symbol_score * 0.65 + global_score * 0.25)

        if best_patterns:
            top_patterns = [p for p in best_patterns if p.get("win_rate", 0) >= 0.75]
            if top_patterns:
                composite += 0.18
            elif any(p.get("win_rate", 0) >= 0.65 for p in best_patterns):
                composite += 0.12

        if (macro in ("bullish", "BULL") and composite >= 0.60) or composite >= 0.70:
            if "CRITICAL" not in str(risk.get("summary", "")) and "STOP" not in str(risk.get("recommendation", "")):
                if final_confidence >= self.MIN_CONFIDENCE:
                    decision = "BUY"
                    reason   = f"Signal fort + confiance {final_confidence:.1%} ≥ {self.MIN_CONFIDENCE*100:.0f}%"

        if decision == "HOLD" and auto_rules and final_confidence >= self.MIN_CONFIDENCE:
            buy_rules = [r for r in auto_rules if "Checkmark" in r or "buy" in r.lower()]
            if len(buy_rules) >= 2 and composite >= 0.60:
                decision = "BUY"
                reason   = f"Règles automatiques ({len(buy_rules)}) + confiance {final_confidence:.1%}"

        if decision == "HOLD":
            confidence = round(max(0.20, composite * 0.65), 2)
        else:
            confidence = round(min(1.00, max(self.MIN_CONFIDENCE, composite * 1.2)), 2)

        natural_summary = (
            f"Analyse {symbol} après {debate_rounds} rounds de débat. "
            f"Confiance collective : {final_confidence:.1%}. "
            f"Score composite : {composite:.2f}. "
            f"Décision : {decision}. {reason}."
        )

        return {
            "agent":         self.name,
            "symbol":        symbol,
            "decision":      decision,
            "confidence":    confidence,
            "symbol_score":  round(symbol_score, 2),
            "global_score":  round(global_score, 2),
            "composite":     round(composite, 2),
            "summary":       natural_summary,
            "reason":        reason,
            "macro":         macro,
            "full_summary":  natural_summary,
            "debate_rounds": debate_rounds,
            "glossary_used": True
        }

    def _hold(self, symbol: str, reason: str, symbol_score: float, global_score: float) -> Dict[str, Any]:
        natural_hold = (
            f"HOLD sur {symbol}. {reason}. "
            f"Score symbole : {symbol_score:.2f} | Score global : {global_score:.2f}."
        )
        return {
            "agent":        self.name,
            "symbol":       symbol,
            "decision":     "HOLD",
            "confidence":   round(max(0.20, symbol_score * 0.65), 2),
            "symbol_score": round(symbol_score, 2),
            "global_score": round(global_score, 2),
            "summary":      natural_hold,
            "reason":       reason,
            "full_summary": natural_hold
        }
