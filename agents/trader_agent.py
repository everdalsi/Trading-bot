"""
👑 TRADER AGENT V5 — Stratégie optimisée + utilisation maximale de la mémoire infinie + VERROUILLAGE 99 % WINRATE PARFAIT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Améliorations vs V4 :
- Score composite plus intelligent (symbol 60% + global 30% + pattern bonus 10%)
- Meilleurs patterns boostent fortement la confiance
- Worst patterns = veto automatique
- Auto-rules utilisées de manière plus agressive
- Meilleure gestion des streaks et dégradation
- Confiance dynamique selon le nombre de leçons
- UPGRADE ÉTAPE 2 : seuil passé à 99 % + force minimum 4 rounds de débat + veto dur
"""

from agents.base_agent import BaseAgent
from typing import Dict, Any


class TraderAgent(BaseAgent):

    def __init__(self):
        super().__init__(
            name="trader",
            role="Décision trading (BUY / SELL / HOLD) — ULTRA CONSERVATEUR : seulement si confiance ≥ 99 %"
        )

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        extreme_learning = context.get("extreme_learning_mode", False) or context.get("learning_mode", False)
        precision_mode   = context.get("precision_mode", False)

        if extreme_learning:
            composite = (context.get("symbol_score", 0.5) * 0.6 + 
                        context.get("global_score", 0.5) * 0.3 + 
                        0.1)
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
        debate_rounds  = context.get("debate_rounds", 0)
        final_confidence = context.get("final_confidence", 0.0)

        if degraded:
            return self._hold(symbol, "Performance dégradée — pause prudente", symbol_score, global_score)

        if streak_type == "loss" and streak_count >= 5:
            return self._hold(symbol, f"Série de {streak_count} pertes consécutives — attendre", symbol_score, global_score)

        if symbol_score < 0.28 and lesson_count >= 20:
            return self._hold(symbol, f"Score symbole insuffisant ({symbol_score:.1%}) — éviter", symbol_score, global_score)

        current_patterns = context.get("patterns", [])
        bad_pattern_names = {p.get("pattern", "") for p in worst_patterns if p.get("win_rate", 1.0) < 0.35}
        for pat in current_patterns:
            pat_name = pat.get("name", "") if isinstance(pat, dict) else str(pat)
            if pat_name in bad_pattern_names:
                return self._hold(symbol, f"Pattern blacklisté détecté : {pat_name}", symbol_score, global_score)

        memory = context.get("memory", {})
        recent_trades = (memory.get("trades", []) or [])[-15:]
        same_symbol = [t for t in recent_trades if t.get("symbol") == symbol]
        severe_losses = sum(1 for t in same_symbol if t.get("pnl_pct", 0) <= -90)
        if len(same_symbol) >= 4 or severe_losses >= 2:
            return self._hold(symbol, f"Anti-spam : {len(same_symbol)} trades récents dont {severe_losses} SL sévères sur {symbol}", symbol_score, global_score)

        # === UPGRADE ÉTAPE 2 : VERROUILLAGE STRICT 99 % + MINIMUM 4 ROUNDS DE DÉBAT ===
        if final_confidence < 0.99 or debate_rounds < 4:
            return self._hold(symbol, f"Confiance collective seulement {final_confidence:.1%} après {debate_rounds} rounds de débat — veto total pour winrate parfait", symbol_score, global_score)

        decision = "HOLD"
        reason   = "Pas de signal clair à ≥ 99 % de confiance"

        composite = (symbol_score * 0.65 + global_score * 0.25)

        if best_patterns:
            top_patterns = [p for p in best_patterns if p.get("win_rate", 0) >= 0.75]
            if top_patterns:
                composite += 0.18
            elif any(p.get("win_rate", 0) >= 0.65 for p in best_patterns):
                composite += 0.12

        if (macro in ("bullish", "BULL") and composite >= 0.92) or composite >= 0.95:
            if ("CRITICAL" not in str(risk.get("summary", "")) and "STOP" not in str(risk.get("recommendation", "")) and final_confidence >= 0.99):
                decision = "BUY"
                reason   = f"Signal ultra-fort + confiance collective {final_confidence:.1%} ≥ 99 %"

        if decision == "HOLD" and auto_rules and final_confidence >= 0.99:
            buy_rules = [r for r in auto_rules if "Checkmark" in r]
            if len(buy_rules) >= 3 and composite >= 0.90:
                decision = "BUY"
                reason   = f"Règles automatiques très solides ({len(buy_rules)}) + confiance {final_confidence:.1%}"

        if decision == "HOLD":
            confidence = round(max(0.20, composite * 0.65), 2)
        else:
            confidence = round(min(1.00, max(0.99, composite * 1.3)), 2)

        natural_summary = (
            f"Salut ! J’ai tout regardé avec l’équipe après {debate_rounds} rounds de débat. "
            f"On est sur {symbol}. Après avoir analysé les leçons passées, "
            f"les stats live, le risque et la confiance collective, je pense qu’on devrait "
            f"{decision.lower() if decision != 'HOLD' else 'rester en attente pour l’instant'}. "
            f"{reason}. "
            f"On a déjà {lesson_count} leçons en mémoire, donc on sait vraiment ce qu’on fait. Objectif : 100 % winrate."
        )

        return {
            "agent":        self.name,
            "symbol":       symbol,
            "decision":     decision,
            "confidence":   confidence,
            "symbol_score": round(symbol_score, 2),
            "global_score": round(global_score, 2),
            "composite":    round(composite, 2),
            "summary":      natural_summary,
            "reason": reason,
            "macro":  macro,
            "full_summary": natural_summary,
            "debate_rounds": debate_rounds
        }

    def _hold(self, symbol: str, reason: str, symbol_score: float, global_score: float) -> Dict[str, Any]:
        natural_hold = (
            f"Salut ! Après avoir tout vérifié avec l’équipe, je préfère qu’on reste en attente sur {symbol}. "
            f"{reason}. "
            f"C’est plus prudent vu les leçons qu’on a accumulées et notre objectif 100 % winrate."
        )
        return {
            "agent":        self.name,
            "symbol":       symbol,
            "decision":     "HOLD",
            "confidence":   0.25,
            "symbol_score": round(symbol_score, 2),
            "global_score": round(global_score, 2),
            "summary":      natural_hold,
            "reason": reason,
        }
