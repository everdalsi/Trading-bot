"""
📊 BACKTEST VALIDATOR AGENT V1.0 — Validation stratégies avant push Git
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Rôle : Avant qu'EvolutionAgent pousse une stratégie sur Git,
       ce validateur tourne un mini-backtest vectorbt sur 7 jours.
       Si le résultat est négatif → bloque le push.
Priorité : MOYENNE — Résout Bug 5 (push de stratégies non testées).
"""

import asyncio
import time
import requests
import os
from typing import Dict, Any, List, Optional

from agents.base_agent import BaseAgent
from logging_config import logger

# Seuils de validation
MIN_WIN_RATE           = 0.40    # WR minimum pour valider
MIN_SHARPE             = 0.50    # Sharpe minimum
MIN_PROFIT_FACTOR      = 1.10    # PF minimum (profits > pertes * 1.10)
MAX_DRAWDOWN_BACKTEST  = -0.15   # Drawdown max toléré sur backtest -15%
MIN_TRADES_BACKTEST    = 5       # Minimum de trades pour valider le test

BACKTEST_DAYS          = 7       # Jours de backtest
BINANCE_BASE           = "https://api.binance.com"


class BacktestValidatorAgent(BaseAgent):
    """
    Tourne un mini-backtest basé sur les signaux de la stratégie proposée
    avant de valider un push Git par EvolutionAgent.
    Utilise les données historiques Binance (pas de vectorbt nécessaire).
    """

    def __init__(self):
        super().__init__(
            name="backtest_validator",
            role=(
                "Validation stratégies via mini-backtest 7 jours avant push Git — "
                "bloque si résultat négatif (WR < 40%, PF < 1.1, DD < -15%)"
            )
        )
        self._last_validation: Optional[dict] = None
        self._last_validated_ts: float        = 0.0

    # ── Domaine ────────────────────────────────────────────────────────────
    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        return any(kw in q for kw in [
            "backtest", "valider", "validation", "stratégie", "push",
            "git push", "backtest_validator", "tester stratégie",
        ]) or super()._is_in_my_domain(question)

    # ── Fetch données historiques ───────────────────────────────────────────
    def _fetch_ohlcv(self, symbol: str, interval: str = "1h", days: int = 7) -> Optional[List[dict]]:
        """Récupère les données OHLCV Binance pour le backtest."""
        try:
            sym    = symbol.upper()
            if not sym.endswith("USDT"):
                sym += "USDT"
            limit  = min(days * 24, 1000)
            url    = f"{BINANCE_BASE}/api/v3/klines"
            params = {"symbol": sym, "interval": interval, "limit": limit}
            resp   = requests.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                return None
            klines = resp.json()
            return [
                {
                    "ts":     int(k[0]),
                    "open":   float(k[1]),
                    "high":   float(k[2]),
                    "low":    float(k[3]),
                    "close":  float(k[4]),
                    "volume": float(k[5]),
                }
                for k in klines
            ]
        except Exception as e:
            logger.warning(f"[BACKTEST_VALIDATOR] OHLCV fetch error {symbol}: {e}")
            return None

    # ── Indicateurs simples (sans numpy/pandas requis) ─────────────────────
    @staticmethod
    def _compute_rsi(closes: List[float], period: int = 14) -> List[float]:
        if len(closes) < period + 1:
            return [50.0] * len(closes)
        rsi_values = [50.0] * period
        gains, losses = [], []
        for i in range(1, period + 1):
            diff = closes[i] - closes[i - 1]
            gains.append(max(0, diff))
            losses.append(max(0, -diff))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        for i in range(period, len(closes)):
            diff     = closes[i] - closes[i - 1]
            g        = max(0, diff)
            l        = max(0, -diff)
            avg_gain = (avg_gain * (period - 1) + g) / period
            avg_loss = (avg_loss * (period - 1) + l) / period
            if avg_loss == 0:
                rsi_values.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_values.append(100 - 100 / (1 + rs))
        return rsi_values

    @staticmethod
    def _compute_ema(closes: List[float], period: int) -> List[float]:
        if len(closes) < period:
            return closes[:]
        ema    = [sum(closes[:period]) / period]
        mult   = 2 / (period + 1)
        for c in closes[period:]:
            ema.append(c * mult + ema[-1] * (1 - mult))
        return [ema[0]] * (period - 1) + ema

    # ── Backtest simplifié (stratégie EMA + RSI) ────────────────────────────
    def _run_backtest(
        self,
        candles: List[dict],
        strategy_params: dict
    ) -> dict:
        """
        Backtest simplifié avec stratégie EMA crossover + RSI filter.
        strategy_params peut override les paramètres par défaut.
        """
        closes    = [c["close"] for c in candles]
        ema_fast  = strategy_params.get("ema_fast", 9)
        ema_slow  = strategy_params.get("ema_slow", 21)
        rsi_buy   = strategy_params.get("rsi_buy", 45)
        rsi_sell  = strategy_params.get("rsi_sell", 60)
        stop_loss = strategy_params.get("stop_loss", -0.025)    # -2.5%
        take_prof = strategy_params.get("take_profit", 0.035)   # +3.5%
        fee       = strategy_params.get("fee", 0.001)           # 0.1% Binance

        ema_f = self._compute_ema(closes, ema_fast)
        ema_s = self._compute_ema(closes, ema_slow)
        rsi   = self._compute_rsi(closes)

        capital    = 1000.0
        peak_cap   = 1000.0
        max_dd     = 0.0
        trades     = []
        position   = None

        for i in range(max(ema_slow, 14), len(candles)):
            price = closes[i]

            # Entrée
            if position is None:
                cross_up  = ema_f[i] > ema_s[i] and ema_f[i - 1] <= ema_s[i - 1]
                rsi_ok    = rsi[i] < rsi_buy
                if cross_up and rsi_ok:
                    position = {
                        "entry_price": price * (1 + fee),
                        "entry_idx":   i,
                        "size":        capital * 0.95 / price,
                    }

            # Sortie
            elif position is not None:
                entry     = position["entry_price"]
                pnl_pct   = (price - entry) / entry
                cross_dn  = ema_f[i] < ema_s[i] and ema_f[i - 1] >= ema_s[i - 1]
                rsi_sell_ok = rsi[i] > rsi_sell

                exit_now = (
                    pnl_pct <= stop_loss
                    or pnl_pct >= take_prof
                    or (cross_dn and rsi_sell_ok)
                )

                if exit_now:
                    exit_price = price * (1 - fee)
                    pnl_usd    = position["size"] * (exit_price - entry)
                    capital   += pnl_usd
                    peak_cap   = max(peak_cap, capital)
                    dd         = (capital - peak_cap) / peak_cap
                    max_dd     = min(max_dd, dd)

                    trades.append({
                        "entry": entry,
                        "exit":  exit_price,
                        "pnl":   pnl_usd,
                        "pct":   pnl_pct * 100,
                        "won":   pnl_usd > 0,
                    })
                    position = None

        if not trades:
            return {
                "valid":          False,
                "reason":         "Aucun trade généré sur 7 jours",
                "n_trades":       0,
                "win_rate":       0.0,
                "profit_factor":  0.0,
                "sharpe":         0.0,
                "max_dd":         0.0,
                "total_return":   0.0,
            }

        n_trades    = len(trades)
        wins        = [t for t in trades if t["won"]]
        losses      = [t for t in trades if not t["won"]]
        win_rate    = len(wins) / n_trades
        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss   = abs(sum(t["pnl"] for t in losses)) or 1e-9
        pf           = gross_profit / gross_loss
        total_return = (capital - 1000.0) / 1000.0

        returns_list = [t["pct"] / 100 for t in trades]
        if len(returns_list) > 1:
            import statistics as _stats
            mean_r  = _stats.mean(returns_list)
            std_r   = _stats.stdev(returns_list) or 1e-9
            sharpe  = mean_r / std_r * (252 ** 0.5)
        else:
            sharpe  = 0.0

        # Validation
        issues = []
        if n_trades < MIN_TRADES_BACKTEST:
            issues.append(f"Trop peu de trades ({n_trades} < {MIN_TRADES_BACKTEST})")
        if win_rate < MIN_WIN_RATE:
            issues.append(f"WinRate trop faible ({win_rate:.0%} < {MIN_WIN_RATE:.0%})")
        if pf < MIN_PROFIT_FACTOR:
            issues.append(f"Profit Factor trop faible ({pf:.2f} < {MIN_PROFIT_FACTOR})")
        if max_dd < MAX_DRAWDOWN_BACKTEST:
            issues.append(f"Drawdown trop élevé ({max_dd:.1%} < {MAX_DRAWDOWN_BACKTEST:.0%})")
        if total_return < 0:
            issues.append(f"Résultat négatif ({total_return:.1%})")

        valid = len(issues) == 0

        return {
            "valid":          valid,
            "reason":         "; ".join(issues) if issues else "OK",
            "n_trades":       n_trades,
            "win_rate":       round(win_rate, 3),
            "profit_factor":  round(pf, 3),
            "sharpe":         round(sharpe, 3),
            "max_dd":         round(max_dd, 3),
            "total_return":   round(total_return, 4),
        }

    # ── Respond ─────────────────────────────────────────────────────────────
    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        symbol          = context.get("symbol", "BTCUSDT")
        strategy_params = context.get("strategy_params", {})
        backtest_days   = context.get("backtest_days", BACKTEST_DAYS)

        loop = asyncio.get_event_loop()

        # Fetch données
        candles = await loop.run_in_executor(
            None, lambda: self._fetch_ohlcv(symbol, days=backtest_days)
        )

        if not candles or len(candles) < 30:
            return {
                "agent":          self.name,
                "summary":        f"⚠️ Backtest impossible — données insuffisantes ({len(candles) if candles else 0} bougies)",
                "confidence":     0.0,
                "recommendation": "HOLD — Backtest impossible",
                "valid":          False,
                "push_allowed":   False,
            }

        # Run backtest
        result = await loop.run_in_executor(
            None, lambda: self._run_backtest(candles, strategy_params)
        )

        self._last_validation    = result
        self._last_validated_ts  = time.time()

        valid         = result["valid"]
        push_allowed  = valid

        if valid:
            return {
                "agent":          self.name,
                "summary":        (
                    f"✅ Backtest {symbol} VALIDÉ ({backtest_days}j) | "
                    f"WR:{result['win_rate']:.0%} PF:{result['profit_factor']:.2f} "
                    f"DD:{result['max_dd']:.1%} Ret:{result['total_return']:.1%}"
                ),
                "arguments": [
                    f"Trades: {result['n_trades']}",
                    f"Win Rate: {result['win_rate']:.0%} (min {MIN_WIN_RATE:.0%})",
                    f"Profit Factor: {result['profit_factor']:.2f} (min {MIN_PROFIT_FACTOR})",
                    f"Sharpe: {result['sharpe']:.2f}",
                    f"Max Drawdown: {result['max_dd']:.1%}",
                    f"Retour total: {result['total_return']:.1%}",
                ],
                "risks":          [],
                "confidence":     0.85,
                "recommendation": "PUSH AUTORISÉ — Backtest positif",
                "valid":          True,
                "push_allowed":   True,
                **result,
            }

        return {
            "agent":          self.name,
            "summary":        (
                f"🛑 Backtest {symbol} ÉCHOUÉ ({backtest_days}j) → PUSH BLOQUÉ | "
                f"Raison: {result['reason']}"
            ),
            "arguments": [
                f"Problème: {result['reason']}",
                f"Trades: {result['n_trades']}",
                f"Win Rate: {result['win_rate']:.0%}",
                f"Profit Factor: {result['profit_factor']:.2f}",
                f"Max Drawdown: {result['max_dd']:.1%}",
                f"Retour total: {result['total_return']:.1%}",
            ],
            "risks":          ["Stratégie non rentable sur backtest 7 jours"],
            "confidence":     0.90,
            "recommendation": "PUSH BLOQUÉ — Améliorer stratégie avant push",
            "valid":          False,
            "push_allowed":   False,
            **result,
        }

    # ── API publique pour EvolutionAgent ────────────────────────────────────
    async def validate_before_push(
        self, symbol: str = "BTCUSDT", strategy_params: dict = None
    ) -> bool:
        """
        Appelé directement par EvolutionAgent avant tout git push.
        Retourne True si le push est autorisé, False sinon.
        """
        result = await self.respond(
            "valider stratégie avant push git",
            {"symbol": symbol, "strategy_params": strategy_params or {}}
        )
        allowed = result.get("push_allowed", False)
        logger.info(
            f"[BACKTEST_VALIDATOR] Push {'AUTORISÉ' if allowed else 'BLOQUÉ'} "
            f"pour {symbol} | {result.get('reason', '')}"
        )
        return allowed
