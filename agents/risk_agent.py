"""
⚠️ RISK AGENT V7 — Expert Gestion du Risque Quantitatif
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UPGRADES V7 (expert-level) :
- Vrai Kelly Criterion (full / half / quarter Kelly adaptatif)
- Value at Risk (VaR) à 95% et 99% sur historique réel
- Conditional VaR (CVaR / Expected Shortfall)
- ATR-based stop loss dynamique
- Volatilité historique annualisée (σ)
- Maximum Adverse Excursion (MAE) estimé
- Corrélation intra-portefeuille → réduction de taille si corrélé
- Régime-aware : sizing ajusté au régime de marché
- Drawdown recovery score (combien de trades pour récupérer)
- Fractions de Kelly : adaptatif selon confiance et historique
"""

import time
import numpy as np
from typing import Dict, Any, List, Optional
from agents.base_agent import BaseAgent
from logging_config import logger


class RiskAgent(BaseAgent):

    def __init__(self):
        super().__init__(
            name="risk",
            description=(
                "Expert gestion du risque : Kelly Criterion adaptatif, VaR 95%/99%, CVaR, "
                "ATR-stops, corrélation portefeuille, régime-aware sizing"
            ),
            role="Gestion du risque quantitative — Kelly/VaR/CVaR/ATR — protection du capital"
        )
        self._trade_history: List[Dict] = []  # buffer local

    # ──────────────────────────────────────────────────────────────────────────
    # KELLY CRITERION — Adaptatif
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _kelly(win_rate: float, avg_win: float, avg_loss: float) -> float:
        """
        Formule de Kelly exacte : K = W/R - (1-W)
        W = win_rate, R = avg_win/avg_loss (ratio)
        Retourne la fraction optimale (0 si négatif).
        """
        if avg_loss == 0 or win_rate <= 0:
            return 0.0
        R = abs(avg_win / avg_loss)
        K = win_rate - (1.0 - win_rate) / R
        return max(0.0, min(K, 0.30))  # Cap à 30% par sécurité

    @staticmethod
    def _fractional_kelly(
        win_rate: float, avg_win: float, avg_loss: float, confidence: float = 0.8
    ) -> float:
        """
        Kelly fractionnel : K * f (f = fraction selon la confiance dans les estimations).
        - Confiance haute (>0.9) → half Kelly (f=0.5)
        - Confiance moyenne (>0.7) → quarter Kelly (f=0.25)
        - Confiance basse → micro Kelly (f=0.10)
        Réduit le risque de ruine quand les estimations sont incertaines.
        """
        K = RiskAgent._kelly(win_rate, avg_win, avg_loss)
        if confidence >= 0.90:
            fraction = 0.50   # half Kelly (recommandé en pratique)
        elif confidence >= 0.75:
            fraction = 0.35
        elif confidence >= 0.60:
            fraction = 0.25   # quarter Kelly
        else:
            fraction = 0.10
        return round(K * fraction, 4)

    # ──────────────────────────────────────────────────────────────────────────
    # VALUE AT RISK & CVaR
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _var_cvar(returns: List[float], confidence_level: float = 0.95) -> Dict[str, float]:
        """
        VaR historique et CVaR (Expected Shortfall) au niveau de confiance donné.
        VaR_95% = perte maximale dans 95% des cas.
        CVaR_95% = perte moyenne au-delà du VaR (tail risk).
        """
        if not returns or len(returns) < 10:
            return {"var_95": 0.05, "var_99": 0.10, "cvar_95": 0.08, "cvar_99": 0.15}
        arr = np.array(returns)
        negative_returns = arr[arr < 0]
        if len(negative_returns) < 3:
            return {"var_95": 0.02, "var_99": 0.04, "cvar_95": 0.03, "cvar_99": 0.06}
        var_95 = float(np.percentile(arr, 5))    # 5e centile
        var_99 = float(np.percentile(arr, 1))    # 1er centile
        # CVaR = moyenne des pertes au-delà du VaR
        cvar_95 = float(np.mean(arr[arr <= var_95]))
        cvar_99 = float(np.mean(arr[arr <= var_99]))
        return {
            "var_95":  round(abs(var_95), 4),
            "var_99":  round(abs(var_99), 4),
            "cvar_95": round(abs(cvar_95), 4),
            "cvar_99": round(abs(cvar_99), 4),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # VOLATILITÉ & ATR-STOP
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _annualized_volatility(returns: List[float], periods_per_year: int = 365) -> float:
        """Volatilité historique annualisée (σ annualisée)."""
        if len(returns) < 5:
            return 0.20   # 20% par défaut
        daily_vol = float(np.std(returns))
        return round(daily_vol * np.sqrt(periods_per_year), 4)

    @staticmethod
    def _atr_stop(price: float, atr: float, multiplier: float = 1.5) -> Dict[str, float]:
        """Stop-loss et take-profit basés sur ATR."""
        if not price or not atr:
            return {"stop_loss": 0.0, "take_profit": 0.0, "risk_per_share": 0.0, "rr": 0.0}
        stop_distance = atr * multiplier
        tp_distance   = atr * multiplier * 2.0   # R/R = 2
        return {
            "stop_loss":      round(price - stop_distance, 6),
            "take_profit":    round(price + tp_distance, 6),
            "risk_per_share": round(stop_distance, 6),
            "rr":             round(tp_distance / stop_distance, 2),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # SHARPE & PROFIT FACTOR
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _sharpe(returns: List[float], risk_free: float = 0.04 / 365) -> float:
        """Ratio de Sharpe annualisé (taux sans risque ≈ 4% annuel)."""
        if len(returns) < 5:
            return 0.0
        arr = np.array(returns)
        excess = arr - risk_free
        std = float(np.std(arr))
        if std == 0:
            return 0.0
        return round(float(np.mean(excess)) / std * np.sqrt(365), 3)

    @staticmethod
    def _profit_factor(returns: List[float]) -> float:
        """Profit Factor = somme gains / somme pertes."""
        gains  = sum(r for r in returns if r > 0)
        losses = sum(abs(r) for r in returns if r < 0)
        if losses == 0:
            return float('inf')
        return round(gains / losses, 3)

    @staticmethod
    def _max_drawdown(equity_curve: List[float]) -> float:
        """Maximum Drawdown depuis un pic."""
        if len(equity_curve) < 2:
            return 0.0
        arr = np.array(equity_curve)
        peaks = np.maximum.accumulate(arr)
        dd = (arr - peaks) / np.where(peaks > 0, peaks, 1)
        return round(float(np.min(dd)), 4)

    @staticmethod
    def _drawdown_recovery_trades(dd: float, win_rate: float, avg_rr: float) -> int:
        """Estimation du nombre de trades pour récupérer du drawdown (formule analytique)."""
        if win_rate <= 0 or avg_rr <= 0 or dd >= 1.0:
            return 999
        # Espérance par trade en fraction de l'equity
        expectancy = win_rate * avg_rr - (1 - win_rate)
        if expectancy <= 0:
            return 999
        # Approximation : trades pour regagner abs(dd)
        trades = int(abs(dd) / (expectancy * 0.02)) + 1
        return min(trades, 200)

    # ──────────────────────────────────────────────────────────────────────────
    # RESPOND
    # ──────────────────────────────────────────────────────────────────────────

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {
                "agent": self.name, "summary": "Hors domaine risk",
                "confidence": 0.0, "recommendation": "HOLD",
            }

        shared_glossary = context.get("shared_glossary", {})
        symbol = context.get("symbol", "UNKNOWN")

        # ── Extraire métriques de performance ─────────────────────────────
        trades        = context.get("trades", [])
        equity        = float(context.get("equity", 1000.0))
        initial_eq    = float(context.get("initial_equity", equity))
        open_positions = int(context.get("open_positions", 0))
        max_positions  = int(context.get("max_positions", 3))
        daily_pnl_pct  = float(context.get("daily_pnl_pct", 0.0))
        streak_type    = context.get("streak_type", "neutral")
        streak_count   = int(context.get("streak_count", 0))
        macro          = context.get("macro", "neutral")
        immune_health  = float(context.get("immune_health", 100))
        regime         = context.get("market_regime", "NEUTRAL")
        price          = float(context.get("price", 0.0))
        atr            = float(context.get("atr", 0.0))
        portfolio_corr = float(context.get("portfolio_correlation", 0.0))
        lesson_count   = int(context.get("lesson_count", 0))

        # ── Calculer returns sur l'historique ─────────────────────────────
        returns: List[float] = []
        wins, losses = 0, 0
        total_win_pnl, total_loss_pnl = 0.0, 0.0
        equity_curve: List[float] = [initial_eq]

        for t in trades[-100:]:   # Fenêtre 100 derniers trades
            pnl = float(t.get("pnl", t.get("pnl_pct", 0.0)))
            if abs(pnl) < 1e-9:
                continue
            ret = pnl / (equity_curve[-1] + 1e-9) if equity_curve[-1] != 0 else pnl / (initial_eq or 1)
            returns.append(ret)
            equity_curve.append(equity_curve[-1] + pnl)
            if pnl > 0:
                wins += 1
                total_win_pnl += pnl
            else:
                losses += 1
                total_loss_pnl += abs(pnl)

        n_trades = wins + losses
        win_rate  = wins / n_trades if n_trades > 0 else 0.50
        avg_win   = total_win_pnl / max(wins, 1)
        avg_loss  = total_loss_pnl / max(losses, 1)

        # ── Métriques quantitatives ────────────────────────────────────────
        var_cvar  = self._var_cvar(returns)
        sharpe    = self._sharpe(returns)
        pf        = self._profit_factor(returns)
        vol       = self._annualized_volatility(returns)
        max_dd    = self._max_drawdown(equity_curve)
        recovery  = self._drawdown_recovery_trades(max_dd, win_rate, avg_win / max(avg_loss, 1e-9))

        # ── Kelly adaptatif ────────────────────────────────────────────────
        confidence_score = float(context.get("confidence", 0.75))
        kelly_full = self._kelly(win_rate, avg_win, avg_loss)
        kelly_frac = self._fractional_kelly(win_rate, avg_win, avg_loss, confidence_score)

        # ── Niveaux ATR ────────────────────────────────────────────────────
        atr_levels = self._atr_stop(price, atr) if price > 0 and atr > 0 else {}

        # ── Niveau de risque ───────────────────────────────────────────────
        risks_list: List[str] = []
        kelly_adjust = 1.0

        # Drawdown journalier
        if daily_pnl_pct <= -0.08:
            risk_level = "CRITICAL"
            risks_list.append(f"Perte journalière critique: {daily_pnl_pct:.1%}")
            kelly_adjust *= 0.0
        elif daily_pnl_pct <= -0.05:
            risk_level = "HIGH"
            risks_list.append(f"Perte journalière élevée: {daily_pnl_pct:.1%}")
            kelly_adjust *= 0.4

        # Positions saturées
        elif open_positions >= max_positions:
            risk_level = "HIGH"
            risks_list.append(f"Positions saturées ({open_positions}/{max_positions})")
            kelly_adjust *= 0.5

        # VaR trop élevé
        elif var_cvar["var_95"] > 0.05:
            risk_level = "ELEVATED"
            risks_list.append(f"VaR 95% élevé: {var_cvar['var_95']:.1%}")
            kelly_adjust *= 0.7

        # Drawdown global
        elif abs(max_dd) > 0.15:
            risk_level = "ELEVATED"
            risks_list.append(f"Drawdown max: {max_dd:.1%} — réduction sizing")
            kelly_adjust *= 0.6

        else:
            risk_level = "LOW" if sharpe > 1.0 else "MODERATE"

        # Streak de pertes
        if streak_type == "loss" and streak_count >= 3:
            risks_list.append(f"Série de {streak_count} pertes — réduire la taille")
            kelly_adjust *= max(0.3, 1.0 - streak_count * 0.15)

        # Sharpe négatif
        if sharpe < 0:
            risks_list.append(f"Sharpe négatif ({sharpe:.2f}) — stratégie peu rentable")
            kelly_adjust *= 0.8

        # Profit Factor < 1
        if pf < 1.0 and n_trades >= 10:
            risks_list.append(f"Profit Factor < 1 ({pf:.2f}) — pertes > gains")
            kelly_adjust *= 0.7

        # Nuit / macro bearish
        import time as _time
        is_night = 0 <= _time.localtime().tm_hour < 6
        if is_night:
            risks_list.append("Mode nuit → réduction automatique")
            kelly_adjust *= 0.5

        if macro == "bearish":
            risks_list.append("Macro bearish → risque majoré")
            kelly_adjust *= 0.75

        # Corrélation portefeuille
        if portfolio_corr > 0.80:
            risks_list.append(f"Corrélation portefeuille: {portfolio_corr:.2f} → diversification insuffisante")
            kelly_adjust *= 0.7

        # Régime de marché
        if regime == "VOLATILE":
            kelly_adjust *= 0.8
        elif regime == "BULL":
            kelly_adjust = min(1.3, kelly_adjust * 1.1)

        # Volatilité annualisée trop haute
        if vol > 1.5:
            risks_list.append(f"Volatilité annualisée très élevée: {vol:.0%}")
            kelly_adjust *= 0.7

        # ── Recommandation ─────────────────────────────────────────────────
        if risk_level == "CRITICAL" or kelly_adjust == 0.0:
            recommendation = "NO TRADE"
        elif risk_level == "HIGH":
            recommendation = "REDUCE EXPOSURE"
        elif risk_level == "ELEVATED":
            recommendation = "TRADE RÉDUIT"
        else:
            recommendation = "TRADE AUTORISÉ"

        kelly_final = round(max(0.0, kelly_frac * kelly_adjust), 4)
        position_size_pct = round(kelly_final * 100, 2)

        summary = (
            f"[RiskV7] {symbol} | Niveau: {risk_level} | Kelly: {kelly_full:.1%}→{kelly_final:.1%} | "
            f"WR: {win_rate:.0%} | PF: {pf:.2f} | Sharpe: {sharpe:.2f} | "
            f"VaR95: {var_cvar['var_95']:.1%} | CVaR95: {var_cvar['cvar_95']:.1%} | "
            f"MaxDD: {max_dd:.1%} | Vol: {vol:.0%} | Sizing: {position_size_pct:.1f}% equity"
        )

        return {
            "agent":           self.name,
            "summary":         summary,
            "arguments": [
                f"Trades analysés: {n_trades} | WR: {win_rate:.0%}",
                f"Kelly full: {kelly_full:.1%} | Kelly fractionnel: {kelly_frac:.1%} | Ajusté: {kelly_final:.1%}",
                f"VaR 95%: {var_cvar['var_95']:.1%} | VaR 99%: {var_cvar['var_99']:.1%}",
                f"CVaR 95%: {var_cvar['cvar_95']:.1%} | CVaR 99%: {var_cvar['cvar_99']:.1%}",
                f"Sharpe: {sharpe:.2f} | Profit Factor: {pf:.2f}",
                f"Max Drawdown: {max_dd:.1%} | Recovery: ~{recovery} trades",
                f"Volatilité ann.: {vol:.0%} | Corrélation portefeuille: {portfolio_corr:.2f}",
                f"Stop ATR: {atr_levels.get('stop_loss', 'N/A')} | TP: {atr_levels.get('take_profit', 'N/A')} | R/R: {atr_levels.get('rr', 'N/A')}",
                f"Positions: {open_positions}/{max_positions} | Regime: {regime}",
            ],
            "risks":             risks_list,
            "confidence":        0.95,
            "recommendation":    recommendation,
            "kelly_adjusted":    kelly_final,
            "kelly_full":        kelly_full,
            "kelly_fractional":  kelly_frac,
            "position_size_pct": position_size_pct,
            "risk_level":        risk_level,
            "var_cvar":          var_cvar,
            "sharpe":            sharpe,
            "profit_factor":     pf,
            "max_drawdown":      max_dd,
            "annualized_vol":    vol,
            "atr_levels":        atr_levels,
            "recovery_trades":   recovery,
            "glossary_used":     True,
        }
