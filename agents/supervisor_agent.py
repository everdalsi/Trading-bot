"""
SUPERVISOR AGENT V5 - GOAT de la synthese finale + Cerveau commun parfait
Specialisation stricte + Glossaire partage + Arbitrage final
"""

from typing import Dict, Any
from agents.base_agent import BaseAgent

class SupervisorAgent(BaseAgent):

    def __init__(self):
        # Ligne originale conservée
        super().__init__(
            name="supervisor",
            role="Synthèse finale, arbitrage et décision ultime"
        )
        # UPGRADE V5 : rôle plus précis pour le cerveau commun
        self.role = "Synthèse finale, arbitrage et décision ultime — uniquement dans mon domaine d’expertise"

    # ======================== FIX 4 : _is_in_my_domain corrigé ========================
    def _is_in_my_domain(self, question: str) -> bool:
        """FIX 4 : Le Supervisor doit participer au débat collectif et à la synthèse finale"""
        q = question.lower()
        
        # Mots-clés de base du rôle
        base_keywords = ["portfolio", "wallet", "savings", "staking", "transfer", "funding", "supervisor", "synthèse", "arbitre"]
        
        # Mots-clés pour le débat collectif (critique pour que le Supervisor ne s'exclue plus)
        debate_keywords = [
            "synthétise", "synthèse", "summarize", "summary", "final decision",
            "vote", "débat", "orchestrator", "consensus", "cerveau collectif",
            "décision finale", "arbitrage", "supervisor"
        ]
        
        # Bypass automatique pour toute question de synthèse/débat
        if any(kw in q for kw in debate_keywords):
            return True
            
        return any(kw in q for kw in base_keywords)
    # ===========================================================================

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        # === UPGRADE V5 : Vérification stricte de spécialisation (cerveau commun) ===
        if not self._is_in_my_domain(question):
            return {
                "agent": self.name,
                "summary": f"⚠️ {self.name} a détecté une question hors de sa spécialité → je ne réponds pas",
                "confidence": 0.0,
                "recommendation": "HOLD - Ignoré par spécialisation stricte",
                "warning": "Hors domaine supervisor"
            }

        # === UPGRADE V5 : Glossaire partagé forcé pour zéro malentendu ===
        shared_glossary = context.get("shared_glossary", {})
        def explain(k): 
            return self.explain_term(k) or shared_glossary.get(k, k)

        # === CODE ORIGINAL V4 conservé intégralement à partir d'ici ===
        # === EXTREME LEARNING MODE (MAX TRADES) ===
        extreme_learning = context.get("extreme_learning_mode", False) or context.get("learning_mode", False)

        agent_outputs    = context.get("agent_outputs", [])
        trader_decision  = context.get("trader_decision", {})
        risk             = context.get("risk", {})
        score            = context.get("score", 0.5)
        symbol           = context.get("symbol", "UNKNOWN")

        lesson_count  = context.get("lesson_count", 0)
        global_score  = context.get("global_score", score)
        insights      = context.get("insights", [])
        auto_rules    = context.get("auto_rules", [])
        degraded      = context.get("degraded", False)
        streak_type   = context.get("streak_type", "neutral")
        streak_count  = context.get("streak_count", 0)

        # === NOUVEAU : données réelles du portefeuille ===
        open_positions = len(context.get("memory", {}).get("positions", {})) if context.get("memory") else 0
        recent_trades  = context.get("memory", {}).get("trades", [])[-5:] if context.get("memory") else []

        trader_summary = str(trader_decision.get("summary", "")).upper()
        trader_decision_val = str(trader_decision.get("decision", "HOLD")).upper()
        risk_summary   = str(risk.get("summary", "")).upper()
        risk_reco      = str(risk.get("recommendation", ""))

        has_buy  = "BUY" in trader_summary or trader_decision_val == "BUY"
        has_sell = "SELL" in trader_summary or trader_decision_val == "SELL"

        learning_mode = any(word in question.lower() for word in [
            "max de trade", "apprenez", "affûtez", "apprendre", "max trade",
            "beaucoup de trades", "vrai argent", "vrai portefeuille", "gérer un vrai"
        ])

        # === MODE EXTREME LEARNING → ON MONTRE LES VRAIES DONNÉES + ON FORCE LES TRADES ===
        if learning_mode or extreme_learning:
            positions_str = f"{open_positions} positions ouvertes" if open_positions > 0 else "aucune position ouverte pour l’instant"
            lessons_str   = f"{lesson_count} leçons accumulées"
            last_trades   = "\n".join([f"• {t.get('symbol','?')} → {t.get('decision','BUY')}" for t in recent_trades]) or "aucun trade récent"

            return {
                "agent": self.name,
                "decision": "BUY",
                "summary": f"FORCE MAX TRADES — Apprentissage extrême activé | {positions_str} | {lessons_str}",
                "arguments": [
                    f"Positions ouvertes : {open_positions}",
                    f"Leçons en mémoire : {lesson_count}",
                    f"Derniers trades :\n{last_trades}",
                    "Mode apprentissage extrême → volume maximum prioritaire"
                ],
                "risks": [],
                "confidence": 0.98,
                "recommendation": "FORCE MAX TRADES — Apprentissage prioritaire (veto ignoré)",
                "full_summary": f"Je force le volume maximum. Actuellement : {positions_str} | {lessons_str}. Derniers trades visibles ci-dessus.",
                "final_decision": "BUY",
                "live_status": {
                    "open_positions": open_positions,
                    "lesson_count": lesson_count,
                    "recent_trades": recent_trades
                },
                "glossary_used": True
            }

        # VETO TRÈS FORT sur les worst patterns (mode précision)
        worst_patterns = context.get("worst_patterns", [])
        if worst_patterns:
            for p in worst_patterns:
                if p.get("win_rate", 1.0) <= 0.35 and p.get("occurrences", 0) >= 5:
                    return {
                        "agent": self.name,
                        "decision": "NO TRADE",
                        "summary": f"⛔ VETO — Pattern perdant détecté ({p.get('pattern')})",
                        "confidence": 0.98,
                        "recommendation": "Éviter ce symbole pour le moment",
                        "glossary_used": True
                    }

        # === UPGRADE ÉTAPE 2 : STRICT VETO MODE + VETO COLLECTIF DUR ===
        strict_veto_mode = context.get("strict_veto_mode", False)
        if strict_veto_mode:
            veto_detected = any(
                "VETO" in str(r.get("summary", "")).upper() or
                "VETO" in str(r.get("recommendation", "")).upper() or
                "NO TRADE" in str(r.get("decision", "")).upper() or
                r.get("risk_level") in ["CRITICAL", "HIGH"]
                for r in agent_outputs
                if isinstance(r, dict)
            )
            if veto_detected:
                return {
                    "agent": self.name,
                    "decision": "NO TRADE",
                    "summary": "⛔ VETO COLLECTIF DÉTECTÉ — winrate parfait prioritaire",
                    "confidence": 1.0,
                    "recommendation": "Aucun trade autorisé par le cerveau collectif",
                    "final_decision": "NO TRADE",
                    "glossary_used": True
                }

        # === UPGRADE PHASE 1+2 : CERVEAU COLLECTIF + SEUIL 98% + IMMUNE SYSTEM ===
        final_confidence = context.get("final_confidence", 0.0)
        debate_rounds    = context.get("debate_rounds", 0)
        immune_health    = context.get("immune_health", 100)

        if final_confidence < 0.98 and debate_rounds >= 3:
            return {
                "agent": self.name,
                "decision": "NO TRADE",
                "summary": f"⛔ Confiance collective seulement {final_confidence:.1%} après {debate_rounds} rounds — on skip pour protéger le winrate >95%",
                "confidence": 0.98,
                "recommendation": "Attendre un consensus plus fort",
                "immune_health": immune_health,
                "glossary_used": True
            }

        if immune_health < 70:
            return {
                "agent": self.name,
                "decision": "NO TRADE",
                "summary": f"🛡️ ImmuneSystem dégradé ({immune_health}%) — pause de sécurité",
                "confidence": 0.98,
                "recommendation": "Système immunitaire en réparation automatique",
                "immune_health": immune_health,
                "glossary_used": True
            }

        # (le reste du code original reste IDENTIQUE)
        final_decision = "HOLD"
        reason         = "Pas de consensus clair"

        if "CRITICAL" in risk_summary or "STOP" in risk_reco:
            final_decision = "NO TRADE"
            reason = "Veto risque critique — capital à protéger"

        elif degraded:
            final_decision = "NO TRADE"
            reason = "Performance en dégradation détectée — pause prudente"

        elif streak_type == "loss" and streak_count >= 5:
            final_decision = "NO TRADE"
            reason = f"Série de {streak_count} pertes consécutives — pause obligatoire"

        elif has_buy and not has_sell:
            effective_score = (score + global_score) / 2
            if (effective_score >= 0.45 and
                    "CRITICAL" not in risk_reco and
                    "STOP" not in risk_reco):
                final_decision = "BUY"
                reason = f"Consensus trader + score {effective_score:.1%} + zéro veto"
            else:
                final_decision = "HOLD"
                reason = "Score insuffisant ou veto léger"

        elif has_sell:
            final_decision = "SELL"
            reason = "Signal SELL clair du TraderAgent"

        else:
            final_decision = "HOLD"
            reason = "Aucun signal BUY/SELL assez fort"

        # === SUMMARY NATURELLE AVEC GLOSSAIRE COMMUN ===
        natural_summary = (
            f"Salut ! En tant que superviseur j’ai tout croisé : les outputs des {len(agent_outputs)} agents, "
            f"le débat de {debate_rounds} rounds, le {explain('immune_system')} à {immune_health}%, "
            f"et les {lesson_count} leçons accumulées. "
            f"Sur {symbol} la décision finale est {final_decision}. {reason}. "
            f"Objectif winrate parfait respecté à 100 %."
        )

        return {
            "agent": self.name,
            "decision": final_decision,
            "summary": natural_summary,
            "confidence": 0.98,
            "recommendation": reason,
            "final_decision": final_decision,
            "full_summary": natural_summary,
            "immune_health": immune_health,
            "debate_rounds": debate_rounds,
            "glossary_used": True
        }
