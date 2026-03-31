"""
👑 TRADER AGENT V8 — Décision trading professionnelle
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Améliorations V8 vs V7 :
- CORRECTION CRITIQUE : mode extreme_learning utilise les signaux réels (BUY/SELL/HOLD)
  au lieu de toujours retourner BUY (dangereux en bear market)
- Score composite affiné : symbol 55% + global 30% + orderbook 10% + sniper 5%
- Veto automatique sur patterns négatifs et risk critique
- Prise en compte du signal order book imbalance pour décision SELL
- Seuil adaptatif selon régime de marché (BULL/BEAR/NEUTRAL)
- Shortcut SELL si bear market confirmé + score < 0.40
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
        symbol       = context.get("symbol", "UNKNOWN")
        symbol_score = context.get("symbol_score", 0.5)
        global_score = context.get("global_score", 0.5)
        ob_imbalance = context.get("orderbook_imb", 0.5)
        sniper_conf  = context.get("sniper_confidence", 0.0)
        sniper_sig   = context.get("sniper_signal", "HOLD")
        regime       = context.get("market_regime", "NEUTRAL")

        if extreme_learning:
            # V8 FIX : utilise les signaux réels, pas toujours BUY
            composite = (
                symbol_score * 0.55 +
                global_score * 0.30 +
                ob_imbalance * 0.10 +
                (sniper_conf if sniper_sig == "BUY" else 0.0) * 0.05
            )
            # Décision basée sur le composite réel
            if composite >= 0.60 and regime != "BEAR":
                decision = "BUY"
            elif composite <= 0.40 or regime == "BEAR":
                decision = "SELL"
            else:
                decision = "HOLD"
            return {
                "agent":      self.name,
                "symbol":     symbol,
                "decision":   decision,
                "recommendation": decision,
                "confidence": round(min(0.92, abs(composite - 0.5) * 2 + 0.5), 2),
                "summary": (
                    f"{symbol} → {decision} (apprentissage) | "
                    f"composite={composite:.2f} | regime={regime} | "
                    f"symbol={symbol_score:.2f} | global={global_score:.2f} | "
                    f"OB={ob_imbalance:.2f}"
                ),
                "reason":    f"Mode apprentissage — signaux: symbol={symbol_score:.2f} global={global_score:.2f} regime={regime}",
                "composite": composite,
            }

        macro          = context.get("macro", "neutral")
        analysis       = context.get("analysis", {})
        risk           = context.get("risk", {})
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
        if current_patterns and any(p.get("name", "") in bad_pattern_names for p in current_patterns):
            return self._hold(symbol, "Pattern négatif historique détecté", symbol_score, global_score)

        # Score composite V8 : 5 sources pondérées
        composite = (
            symbol_score  * 0.55 +
            global_score  * 0.30 +
            ob_imbalance  * 0.10 +
            (sniper_conf if sniper_sig == "BUY" else 0.0) * 0.05
        )

        # Ajustement selon régime
        if regime == "BULL":
            composite = min(1.0, composite * 1.08)
        elif regime == "BEAR":
            composite = max(0.0, composite * 0.90)

        # Décision
        decision = "HOLD"
        reason   = f"Score composite {composite:.2f}"

        if composite >= 0.65:
            decision = "BUY"
            reason   = f"Signal fort haussier (composite={composite:.2f}, regime={regime})"
        elif composite <= 0.35:
            decision = "SELL"
            reason   = f"Signal fort baissier (composite={composite:.2f}, regime={regime})"
        elif composite >= 0.55 and regime == "BULL" and final_confidence >= self.MIN_CONFIDENCE:
            decision = "BUY"
            reason   = f"Bias haussier modéré en bull market (composite={composite:.2f})"
        elif composite <= 0.45 and regime == "BEAR":
            decision = "SELL"
            reason   = f"Bias baissier modéré en bear market (composite={composite:.2f})"

        # Règles automatiques → override HOLD vers BUY
        if decision == "HOLD" and auto_rules and final_confidence >= self.MIN_CONFIDENCE:
            buy_rules = [r for r in auto_rules if "Checkmark" in r or "buy" in r.lower()]
            if len(buy_rules) >= 2 and composite >= 0.60:
                decision = "BUY"
                reason   = f"Règles automatiques ({len(buy_rules)}) + confiance {final_confidence:.1%}"

        if decision == "HOLD":
            confidence = round(max(0.20, composite * 0.65), 2)
        else:
            confidence = round(min(1.00, max(self.MIN_CONFIDENCE, abs(composite - 0.5) * 2 + 0.5)), 2)

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
            "recommendation": decision,
            "confidence":    confidence,
            "symbol_score":  round(symbol_score, 2),
            "global_score":  round(global_score, 2),
            "composite":     round(composite, 2),
            "summary":       natural_summary,
            "reason":        reason,
            "macro":         macro,
            "full_summary":  natural_summary,
            "debate_rounds": debate_rounds,
            "regime":        regime,
            "glossary_used": True
        }

    def _hold(self, symbol: str, reason: str, symbol_score: float, global_score: float) -> Dict[str, Any]:
        natural_hold = (
            f"HOLD sur {symbol}. {reason}. "
            f"Score symbole : {symbol_score:.2f} | Score global : {global_score:.2f}."
        )
        return {
            "agent":          self.name,
            "symbol":         symbol,
            "decision":       "HOLD",
            "recommendation": "HOLD",
            "confidence":     round(max(0.20, symbol_score * 0.65), 2),
            "symbol_score":   round(symbol_score, 2),
            "global_score":   round(global_score, 2),
            "summary":        natural_hold,
            "reason":         reason,
            "full_summary":   natural_hold
        }
