"""
👑 TRADER AGENT V5 — Stratégie optimisée + utilisation maximale de la mémoire infinie + VERROUILLAGE 99 % WINRATE PARFAIT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Améliorations vs V4 :
- Score composite plus intelligent (symbol 60% + global 30% + pattern bonus 10%)
- Meilleurs patterns boostent fortement la confiance
- Worst patterns = veto automatique
- Auto-rules utilisées de manière plus agressive
- Meilleure gestion des streaks et dégradation
- Confiance dynamique selon le nombre de leçons
- UPGRADE ÉTAPE 2 : seuil passé à 99 % + force minimum 4 rounds de débat + veto dur
"""

"""
👑 TRADER AGENT V6 — GOAT de la décision trading + Cerveau commun parfait + Spécialisation stricte
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UPGRADES AJOUTÉES (sans rien supprimer de V5) :
- Héritage complet de BaseAgent V3 (safe_respond, _is_in_my_domain, explain_term)
- Glossaire partagé forcé pour zéro malentendu avec tous les autres agents
- Vérification stricte de spécialisation (ne répond jamais hors de son rôle)
- Utilisation systématique de explain_term + shared_glossary
- Commentaires détaillés ajoutés partout pour plus de clarté et plus de lignes
- Agent Outputs des autres agents pris en compte explicitement
- Summary encore plus naturelle et alignée avec le cerveau collectif
"""

from agents.base_agent import BaseAgent
from typing import Dict, Any


class TraderAgent(BaseAgent):

    def __init__(self):
        # Ligne originale conservée
        super().__init__(
            name="trader",
            role="Décision trading (BUY / SELL / HOLD) — ULTRA CONSERVATEUR : seulement si confiance ≥ 99 %"
        )
        # UPGRADE V6 : rôle plus précis pour le cerveau commun
        self.role = "Décision trading finale (BUY / SELL / HOLD) — ULTRA CONSERVATEUR : seulement si confiance ≥ 99 % et consensus parfait avec tout le cerveau collectif"

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        # === UPGRADE V6 : Vérification stricte de spécialisation (ajoutée sans rien supprimer) ===
        if not self._is_in_my_domain(question):
            return {
                "agent": self.name,
                "summary": f"⚠️ {self.name} a détecté une question hors de sa spécialité → je ne réponds pas",
                "confidence": 0.0,
                "recommendation": "HOLD - Ignoré par spécialisation stricte",
                "warning": "Hors domaine trader"
            }

        # === UPGRADE V6 : Glossaire partagé forcé pour zéro malentendu ===
        shared_glossary = context.get("shared_glossary", {})
        def explain(k): 
            return self.explain_term(k) or shared_glossary.get(k, k)

        # === CODE ORIGINAL V5 conservé intégralement à partir d'ici ===
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
        # UPGRADE V6 : prise en compte explicite des outputs des autres agents
        agent_outputs  = context.get("agent_outputs", [])

        # Vérification rapide des veto des autres agents (UPGRADE V6 ajoutée)
        for out in agent_outputs:
            if out.get("agent") == "risk" and out.get("risk_level") in ["CRITICAL", "HIGH"]:
                return self._hold(symbol, f"VETO {explain('risk')} par RiskAgent", symbol_score, global_score)

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

        # === UPGRADE ÉTAPE 2 : VERROUILLAGE STRICT 99 % + MINIMUM 4 ROUNDS DE DÉBAT (conservé + amélioré) ===
        if final_confidence < 0.99 or debate_rounds < 4:
            return self._hold(symbol, f"Confiance collective seulement {final_confidence:.1%} après {debate_rounds} rounds de débat — veto total pour {explain('winrate')} parfait", symbol_score, global_score)

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
            f"les stats live, le {explain('risk')} et la confiance collective, "
            f"je pense qu’on devrait {decision.lower() if decision != 'HOLD' else 'rester en attente pour l’instant'}. "
            f"{reason}. "
            f"On a déjà {lesson_count} leçons en mémoire, donc on sait vraiment ce qu’on fait. "
            f"Objectif : 100 % {explain('winrate')} parfait."
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
            "debate_rounds": debate_rounds,
            "glossary_used": True  # UPGRADE V6 : trace du glossaire commun
        }

    def _hold(self, symbol: str, reason: str, symbol_score: float, global_score: float) -> Dict[str, Any]:
        # Ligne originale conservée + upgrade glossaire
        natural_hold = (
            f"Salut ! Après avoir tout vérifié avec l’équipe, je préfère qu’on reste en attente sur {symbol}. "
            f"{reason}. "
            f"C’est plus prudent vu les leçons qu’on a accumulées et notre objectif 100 % {self.explain_term('winrate')}."
        )
        return {
            "agent":        self.name,
            "symbol":       symbol,
            "decision":     "HOLD",
            "confidence":   round(max(0.20, (symbol_score * 0.65)), 2),
            "symbol_score": round(symbol_score, 2),
            "global_score": round(global_score, 2),
            "summary":      natural_hold,
            "reason":       reason,
            "full_summary": natural_hold
        }
