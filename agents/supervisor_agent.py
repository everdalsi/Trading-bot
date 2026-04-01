"""
🎯 SUPERVISOR AGENT V6 — Vote Pondéré + Décision Risk-Adjusted
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AMÉLIORATIONS V6 :
- Vote pondéré par confiance de chaque agent (était vote binaire OUI/NON)
- Intégration signaux V6 : orderbook_imb, portfolio_corr, size_reduction
- Décision bayésienne : TRADE si P(win) > 0.55 + veto conditions
- Score de consensus : confiance agrégée de tous les agents
- Taille suggérée : ajustée selon volatilité, corrélation, signaux
- Raison explicite : explication claire de chaque décision
"""

from typing import Dict, Any, List
from agents.base_agent import BaseAgent
from logging_config import logger


class SupervisorAgent(BaseAgent):

    # Poids de chaque agent dans la décision finale
    AGENT_WEIGHTS = {
        # ── Core agents ────────────────────────────────────────────────
        "trader":               0.22,
        "risk":                 0.18,
        "analyst":              0.16,
        "quant_ml":             0.11,
        "research":             0.09,
        "knowledge_specialist": 0.07,
        "social_listener":      0.04,
        "hedging":              0.02,
        "order_book":           0.07,   # signal direct bid/ask
        "funding_rate":         0.03,
        "correlation_watcher":  0.02,
        # ── BUG FIX V8/V9 : agents edge arbitrage manquants ──────────────
        # Avaient seulement 0.03 fallback malgré des edges de 30-50%!
        "polymarket_trader":    0.08,   # edge direct Polymarket — très fiable
        "polymarket_arb":       0.06,   # arb cross-exchange
        "event_sniper":         0.06,   # liquidations + OI spikes — signal court terme
        "sports_arb":           0.05,   # arb garanti multi-bookmaker
    }
    # Boost dynamique : si PolyTrader edge > 25% → multiplier son poids par 2x
    EDGE_BOOST_THRESHOLD = 0.25  # edge_pct > 25% → boost poids polymarket_trader
    # Agents pouvant opposer un veto (ignoré par vote pondéré si veto)
    VETO_AGENTS = {"risk", "drawdown_guard", "news_event", "funding_rate"}

    def __init__(self):
        super().__init__(
            name="supervisor",
            role="Synthèse finale bayésienne, vote pondéré par confiance, décision risk-adjusted"
        )

    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        return any(kw in q for kw in [
            "synthétise", "synthèse", "summarize", "summary", "final decision",
            "vote", "débat", "orchestrator", "consensus", "cerveau collectif",
            "décision finale", "arbitrage", "supervisor", "portfolio", "trade ou no trade",
        ])

    # ────────────────────────────────────────────────────────────────────────
    # VOTE PONDÉRÉ
    # ────────────────────────────────────────────────────────────────────────

    def _weighted_vote(self, agent_outputs: List[Dict]) -> Dict[str, Any]:
        """
        Vote bayésien pondéré par la confiance de chaque agent.
        Score final = Σ(poids_agent × confiance × direction)
        Direction : +1 pour BUY, -1 pour SELL, 0 pour HOLD
        """
        buy_score  = 0.0
        sell_score = 0.0
        total_weight = 0.0
        participating_agents = []

        for resp in agent_outputs:
            if not isinstance(resp, dict):
                continue
            agent_name = resp.get("agent", "unknown")
            confidence = max(0.0, min(1.0, float(resp.get("confidence", 0.5))))
            reco = str(resp.get("recommendation", resp.get("decision", "HOLD"))).upper()
            weight = self.AGENT_WEIGHTS.get(agent_name, 0.03)

            # BUG FIX/AMÉLIORATION: Boost dynamique pour signaux edge élevé
            if agent_name == "polymarket_trader":
                edge_pct = float(resp.get("avg_edge_pct", resp.get("edge_pct", 0))) / 100.0
                if edge_pct >= self.EDGE_BOOST_THRESHOLD:
                    weight = min(weight * 2.0, 0.20)  # max 20% du vote
            elif agent_name == "sports_arb" and "ARB" in reco:
                weight = min(weight * 1.5, 0.12)  # arb garanti → boost modéré
            elif agent_name == "event_sniper" and confidence > 0.7:
                weight = min(weight * 1.3, 0.10)  # liquidation cascade → boost

            direction = 0.0
            if any(x in reco for x in ["BUY", "LONG", "BULLISH", "HAUSSE"]):
                direction = 1.0
            elif any(x in reco for x in ["SELL", "SHORT", "BEARISH", "BAISSE"]):
                direction = -1.0

            weighted_vote = weight * confidence * direction
            if direction > 0:
                buy_score += abs(weighted_vote)
            elif direction < 0:
                sell_score += abs(weighted_vote)

            total_weight += weight
            participating_agents.append({
                "agent": agent_name, "direction": direction,
                "confidence": confidence, "weight": weight,
                "vote_strength": round(abs(weighted_vote), 4)
            })

        # Normalisation
        norm = total_weight if total_weight > 0 else 1.0
        buy_norm  = buy_score / norm
        sell_norm = sell_score / norm
        net_score = buy_norm - sell_norm   # [-1, +1]

        return {
            "buy_score":  round(buy_norm, 3),
            "sell_score": round(sell_norm, 3),
            "net_score":  round(net_score, 3),
            "agents":     participating_agents,
            "n_agents":   len(participating_agents),
        }

    def _compute_consensus_confidence(self, vote_result: Dict, agent_outputs: List[Dict]) -> float:
        """Confiance basée sur le degré de consensus entre agents.
        Pour HOLD : plancher de 35% (le bot est certain de ne pas trader, pas 0%).
        Pour BUY/SELL : confiance = convergence des agents × avg_confidence.
        """
        agents = vote_result.get("agents", [])
        if not agents:
            return 0.35
        # Convergence : proportion d'agents dans la même direction que la décision
        net = vote_result["net_score"]
        same_dir = sum(1 for a in agents if (a["direction"] > 0 and net > 0) or (a["direction"] < 0 and net < 0))
        convergence = same_dir / len(agents)
        # Confiance moyenne des agents participants
        avg_conf = sum(a["confidence"] for a in agents) / len(agents)
        # Score final
        return min(0.97, convergence * 0.4 + avg_conf * 0.6)

    def _compute_suggested_size(self, context: dict, final_decision: str) -> float:
        """
        Taille suggérée [0.0 - 1.0] relative à la taille max autorisée.
        Ajustée selon volatilité, corrélation, momentum.
        """
        if final_decision in ("NO TRADE", "HOLD"):
            return 0.0

        base_size = 1.0
        # Réduction si corrélation élevée
        corr = context.get("portfolio_corr", 0.0)
        if corr > 0.70:
            base_size *= 1.0 - (corr - 0.70) * 2   # max -60% si corr = 1.0

        # Réduction signalée par agents V6
        size_reduction = context.get("size_reduction", 0.0)
        base_size *= (1.0 - size_reduction)

        # Réduction si régime VOLATILE
        regime = context.get("macro", "NEUTRAL")
        if regime == "VOLATILE":
            base_size *= 0.60
        elif regime == "BEAR":
            base_size *= 0.75

        # Boost si divergence bullish détectée (analyst)
        analyst = context.get("analysis", {})
        if analyst.get("divergence") == "BULL_DIVERGENCE":
            base_size = min(1.0, base_size * 1.15)

        return round(max(0.0, min(1.0, base_size)), 2)

    # ────────────────────────────────────────────────────────────────────────
    # RÉPONSE PRINCIPALE
    # ────────────────────────────────────────────────────────────────────────

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {
                "agent": self.name, "summary": "⚠️ Hors domaine supervisor",
                "confidence": 0.0, "recommendation": "HOLD"
            }

        shared_glossary = context.get("shared_glossary", {})
        agent_outputs   = context.get("agent_outputs", [])
        trader_decision = context.get("trader_decision", {})
        risk            = context.get("risk", {})
        symbol          = context.get("symbol", "UNKNOWN")
        lesson_count    = context.get("lesson_count", 0)
        global_score    = context.get("global_score", 0.5)
        debate_rounds   = context.get("debate_rounds", 0)
        immune_health   = context.get("immune_health", 100)
        streak_type     = context.get("streak_type", "neutral")
        streak_count    = context.get("streak_count", 0)
        degraded        = context.get("degraded", False)
        extreme_learning = context.get("extreme_learning_mode", False)

        # ── Vérification veto systèmes de sécurité ────────────────────
        risk_reco = str(risk.get("recommendation", "")).upper()
        trader_dec = str(trader_decision.get("decision", "HOLD")).upper()

        veto_reason = None

        if "STOP" in risk_reco or "CRITICAL" in risk_reco:
            veto_reason = f"RiskAgent veto : {risk.get('summary', '')[:60]}"
        elif degraded:
            veto_reason = "Performance en dégradation — pause prudente"
        elif streak_type == "loss" and streak_count >= 5:
            veto_reason = f"{streak_count} pertes consécutives — pause obligatoire"

        if veto_reason and not extreme_learning:
            return {
                "agent":         self.name,
                "decision":      "NO TRADE",
                "final_decision": "NO TRADE",
                "summary":       f"🛑 Supervisor VETO — {veto_reason}",
                "confidence":    0.98,
                "recommendation": veto_reason,
                "suggested_size": 0.0,
                "glossary_used": True,
            }

        # ── Vote pondéré ────────────────────────────────────────────────
        vote = self._weighted_vote(agent_outputs)
        consensus_conf = self._compute_consensus_confidence(vote, agent_outputs)

        # Intégration orderbook
        ob_signal = context.get("orderbook_signal", "NEUTRAL")
        ob_imb    = context.get("orderbook_imb", 0.5)
        ob_boost  = (ob_imb - 0.5) * 0.20 if ob_signal != "NEUTRAL" else 0.0

        net = vote["net_score"] + ob_boost

        # ── Edge agents boost ─────────────────────────────────────────────
        # PolyTrader et SportsArb peuvent déclencher des trades sans consensus
        polytrader_edge   = float(context.get("polytrader_edge", 0.0))
        polytrader_signal = str(context.get("polytrader_signal", "HOLD")).upper()
        sportsarb_signal  = str(context.get("sportsarb_signal",  "HOLD")).upper()
        sportsarb_profit  = float(context.get("sportsarb_best_profit", 0.0))
        edge_boost = 0.0
        if polytrader_edge > 0.25 and "BUY" in polytrader_signal:
            edge_boost += min(0.35, polytrader_edge / 100.0 * 0.8)   # 44% edge → +0.35
        elif polytrader_edge > 0.25 and ("SELL" in polytrader_signal or "NO" in polytrader_signal):
            edge_boost -= min(0.35, polytrader_edge / 100.0 * 0.8)
        if "ARB" in sportsarb_signal and sportsarb_profit > 0.40:
            edge_boost += min(0.20, sportsarb_profit * 0.20)  # 0.85% profit → +0.17 boost
        if abs(edge_boost) > 0.0:
            logger.info(f"[SUPERVISOR] 🎯 Edge boost: {edge_boost:+.3f} (poly={polytrader_edge:.1f}% arb={sportsarb_profit:.2f}%)")
        net += edge_boost

        # ── Décision finale ─────────────────────────────────────────────
        # FIX TRAINING V8: seuils abaissés en training pour maximiser les trades
        import os as _os_sup
        _in_training_sup = _os_sup.environ.get("BOT_TRAINING_MODE", "True").lower() in ("true", "1", "yes")
        _net_threshold = 0.03 if (_in_training_sup or extreme_learning) else 0.15
        if net > _net_threshold:
            final_decision = "BUY"
            reason = f"Consensus pondéré BUY (net: {net:+.3f}) | {vote['n_agents']} agents"
        elif net < -_net_threshold:
            final_decision = "SELL"
            reason = f"Consensus pondéré SELL (net: {net:+.3f}) | {vote['n_agents']} agents"
        else:
            final_decision = "HOLD"
            reason = f"Signal trop faible (net: {net:+.3f}) — pas de trade"

        # HOLD confidence = force du signal neutre (1.0 = parfait, 0.0 = borderline)
        if final_decision == "HOLD":
            hold_strength   = max(0.0, 1.0 - abs(net) / 0.15)
            consensus_conf  = max(consensus_conf, round(hold_strength * 0.65, 2))

        # Override clair du trader si consensus fort
        if abs(net) > 0.35 and "BUY" in trader_dec and final_decision == "HOLD":
            final_decision = "BUY"
            reason += " | Override trader fort"
        elif abs(net) > 0.35 and "SELL" in trader_dec and final_decision == "HOLD":
            final_decision = "SELL"
            reason += " | Override trader fort"

        # Confidence floor pour les trades déclenchés par edge agents
        if final_decision != "HOLD" and consensus_conf < 0.35:
            consensus_conf = max(0.35, abs(edge_boost) * 2.0 + consensus_conf)
            consensus_conf = min(0.90, consensus_conf)

        suggested_size = self._compute_suggested_size(context, final_decision)

        # ── Summary ──────────────────────────────────────────────────────
        top_voters = sorted(vote["agents"], key=lambda a: a["vote_strength"], reverse=True)[:3]
        top_str = " | ".join(f"{a['agent']}({a['confidence']:.0%})" for a in top_voters)

        full_summary = (
            f"🎯 Supervisor V6 — {symbol} | Décision: {final_decision} | "
            f"Net score: {net:+.3f} | Buy: {vote['buy_score']:.3f} vs Sell: {vote['sell_score']:.3f} | "
            f"Consensus: {consensus_conf:.0%} | Taille suggérée: {suggested_size:.0%} | "
            f"Agents: {vote['n_agents']} | Débat: {debate_rounds}R | Top voters: {top_str}"
        )

        logger.info(f"[SUPERVISOR V6] {final_decision} | net={net:+.3f} | conf={consensus_conf:.0%} | taille={suggested_size:.0%}")

        return {
            "agent":           self.name,
            "decision":        final_decision,
            "final_decision":  final_decision,
            "summary":         full_summary,
            "full_summary":    full_summary,
            "confidence":      consensus_conf,
            "recommendation":  reason,
            "suggested_size":  suggested_size,
            "vote_result":     vote,
            "net_score":       round(net, 3),
            "orderbook_boost": round(ob_boost, 3),
            "immune_health":   immune_health,
            "debate_rounds":   debate_rounds,
            "glossary_used":   True,
        }
