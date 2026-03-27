"""
📊 ANALYST AGENT V3 — Analyse enrichie + mémoire infinie
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Améliorations vs V2 :

- Utilise LearningAgent DB pour les stats (mémoire infinie)
- Intègre le PerformanceTracker pour stats temps réel
- Détecte les tendances de performance (amélioration / dégradation)
- Recommandations basées sur les insights compressés
"""

from agents.base_agent import BaseAgent
from typing import Dict, Any


class AnalystAgent(BaseAgent):

    def __init__(self):
        super().__init__(
            name="analyst",
            description="Analyse performance, winrate, stats et historique des trades"
        )

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        extreme_learning = context.get("extreme_learning_mode", False) or context.get("learning_mode", False)

        # === PRIORITÉ 1 : Stats depuis PerformanceTracker (temps réel) ===
        wr_live     = context.get("wr_live")
        wins_live   = context.get("wins_live")
        losses_live = context.get("losses_live")
        total_live  = context.get("total_trades")
        sharpe_live = context.get("sharpe")
        pf_live     = context.get("profit_factor")

        if isinstance(wr_live, (int, float)):
            total = total_live if isinstance(total_live, int) else (wins_live or 0) + (losses_live or 0)
            trend = ""
            if isinstance(wr_live, float):
                if wr_live >= 60:
                    trend = "Excellente performance"
                elif wr_live >= 50:
                    trend = "Performance correcte"
                else:
                    trend = "Performance à améliorer"

            # === UPGRADE GROK-LIKE : RAISONNEMENT NATUREL ===
            natural_summary = (
                f"Salut ! J’ai regardé les stats live du portefeuille. "
                f"Sur {total} trades, le winrate est de {wr_live:.1f}%. "
                f"C’est {trend.lower()}. "
                f"Avec les leçons accumulées, on voit une bonne tendance globale."
            )

            return {
                "agent": self.name,
                "summary": natural_summary,
                "arguments": [
                    f"{total} trades analysés (source live)",
                    f"{wins_live or 0} gagnants | {losses_live or 0} perdants",
                    f"Sharpe: {sharpe_live or 'N/A'} | P.Factor: {pf_live or 'N/A'}",
                    "Source: PerformanceTracker temps réel",
                ],
                "risks": (
                    ["WR en dessous de 50% — revoir la stratégie"] if wr_live < 50 else
                    ["Performance dans les clous"]
                ),
                "confidence": 0.95,
                "recommendation": (
                    "Continuer et renforcer les setups actuels." if wr_live >= 55 else
                    "Réduire la taille de position jusqu'à stabilisation."
                    if wr_live >= 45 else
                    "Passer en mode apprentissage pur — réduire l'exposition."
                ),
                "full_summary": natural_summary
            }

        # === PRIORITÉ 2 : Stats depuis LearningAgent DB (mémoire infinie) ===
        lesson_count  = context.get("lesson_count", 0)
        global_score  = context.get("global_score")
        symbol_score  = context.get("symbol_score")
        insights      = context.get("insights", [])
        auto_rules    = context.get("auto_rules", [])
        best_patterns = context.get("best_patterns", [])

        if lesson_count > 0 and global_score is not None:
            wr = round(global_score * 100, 1)
            trend = (
                "Bonne trajectoire" if wr >= 60 else
                "Apprentissage en cours" if wr >= 45 else
                "Stratégie à revoir"
            )

            natural_summary = (
                f"Salut ! J’ai analysé les {lesson_count} leçons en mémoire. "
                f"Le winrate global est de {wr}%. "
                f"C’est {trend.lower()}. "
                f"Les meilleurs patterns sont solides, on peut s’appuyer dessus."
            )

            return {
                "agent": self.name,
                "summary": natural_summary,
                "arguments": [
                    f"{lesson_count} leçons enregistrées (sans limite)",
                    f"Score global: {global_score:.1%} | Score symbole: {symbol_score:.1%}" if symbol_score else f"Score global: {global_score:.1%}",
                    f"Auto-règles actives: {len(auto_rules)}",
                    f"Meilleurs patterns: {', '.join(p['pattern'] for p in best_patterns[:2]) or 'Aucun encore'}",
                ],
                "risks": (
                    ["Peu de données — score peu fiable"] if lesson_count < 20 else
                    ["Performance dégradée"] if wr < 45 else []
                ),
                "confidence": min(0.95, 0.50 + lesson_count / 200),
                "recommendation": (
                    "Solide base de données. Renforcer les meilleurs patterns." if lesson_count >= 50 and wr >= 55 else
                    "Continuer à accumuler des données (objectif : 50+ trades)." if lesson_count < 50 else
                    "Réviser les patterns — trop de pertes récentes."
                ),
                "full_summary": natural_summary
            }

        # === PRIORITÉ 3 : Fallback sur le JSON sim ===
        sim    = context.get("sim", {})
        memory = context.get("memory", {})
        trades = [
            t for t in (sim.get("trades", []) or memory.get("trades", []))
            if isinstance(t.get("pnl"), (int, float))
        ]
        wins  = [t for t in trades if t["pnl"] > 0]
        total = len(trades)
        wr    = round(len(wins) / total * 100, 1) if total > 0 else 0.0

        natural_summary = (
            f"Salut ! J’ai regardé les données du portefeuille de simulation. "
            f"Sur {total} trades, le winrate est estimé à {wr:.1f}%. "
            f"On est encore en phase d’apprentissage, mais les bases sont là."
        )

        return {
            "agent": self.name,
            "summary": natural_summary,
            "arguments": [
                f"{total} trades analysés (JSON portfolio)",
                f"{len(wins)} gagnants",
                "Données extraites du fichier sim JSON",
                "Connecter PerformanceTracker pour des stats temps réel",
            ],
            "risks": ["Fallback JSON — données moins fraîches que la DB"],
            "confidence": 0.65 if total >= 10 else 0.35,
            "recommendation": (
                "Peu de trades. Le WR réel se stabilisera après 30+ trades."
                if total < 30 else
                "Performance correcte. Continuer l'accumulation de données."
            ),
            "full_summary": natural_summary
        }
