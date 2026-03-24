from agents.base_agent import BaseAgent
from typing import Dict, Any

class LearningAgent(BaseAgent):
    """
    🧠 LEARNING AGENT V3
    Analyse les trades passés, calcule les scores, ajuste la confiance
    et donne des leçons intelligentes.
    """

    def __init__(self):
        super().__init__(
            name="learning",
            role="Apprentissage automatique, scoring des patterns et ajustement de confiance"
        )

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        memory = context.get("memory", {})
        sim = context.get("sim", {})
        symbol = context.get("symbol")  # si on parle d'un coin précis

        # Utilise les trades réels du bot (clé "pnl")
        trades = sim.get("trades", []) or memory.get("trades", [])

        # === Calcul des scores ===
        def compute_global_score():
            closed = [t for t in trades if isinstance(t.get("pnl"), (int, float))]
            if not closed:
                return 0.5
            wins = sum(1 for t in closed if t["pnl"] > 0)
            return round(wins / len(closed), 3)

        def compute_symbol_score(sym):
            closed = [t for t in trades if t.get("symbol") == sym and isinstance(t.get("pnl"), (int, float))]
            if not closed:
                return 0.5
            wins = sum(1 for t in closed if t["pnl"] > 0)
            return round(wins / len(closed), 3)

        global_score = compute_global_score()
        symbol_score = compute_symbol_score(symbol) if symbol else global_score

        # === Ajustement de confiance ===
        base_conf = context.get("base_confidence", 0.65)
        adjusted_conf = base_conf + (0.18 if symbol_score > 0.65 else -0.22 if symbol_score < 0.40 else 0)
        adjusted_conf = max(0.10, min(0.95, adjusted_conf))

        # === Résumé learning ===
        closed_trades = [t for t in trades if isinstance(t.get("pnl"), (int, float))]
        wins = sum(1 for t in closed_trades if t["pnl"] > 0)
        losses = len(closed_trades) - wins
        total = len(closed_trades)
        winrate = round(wins / total * 100, 1) if total > 0 else 0.0

        # Réponse selon la question
        q = question.lower()
        if any(k in q for k in ["winrate", "wr", "performance", "stat"]):
            summary = f"Winrate global : {winrate}% ({total} trades)"
        elif "blacklist" in q or "risque" in q:
            summary = f"Score {symbol or 'global'} : {symbol_score:.1%} → {'BLACKLIST recommandé' if symbol_score < 0.3 else 'OK'}"
        else:
            summary = f"Learning score : {global_score:.1%} | Symbole {symbol or 'global'} : {symbol_score:.1%}"

        return {
            "agent": self.name,
            "summary": summary,
            "arguments": [
                f"Total trades analysés : {total}",
                f"Wins : {wins} | Losses : {losses}",
                f"Confiance ajustée : {adjusted_conf:.2f}",
                f"Score {symbol or 'global'} : {symbol_score:.1%}"
            ],
            "risks": ["Score < 0.3 → blacklist automatique"] if symbol_score < 0.3 else [],
            "confidence": adjusted_conf,
            "recommendation": (
                "Renforcer les setups sur ce symbole" if symbol_score > 0.65 else
                "Éviter ce symbole pour l'instant" if symbol_score < 0.3 else
                "Continuer à collecter des données"
            )
        }
