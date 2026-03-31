"""
🔗 CORRELATION WATCHER AGENT V1.0 — Surveillance corrélation inter-positions
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Rôle : Surveille la corrélation entre les positions ouvertes.
       Si BTC/ETH/SOL sont toutes longues avec corrélation > 0.8 
       → comme avoir une seule grosse position → réduit automatiquement la taille.
Priorité : MOYENNE
"""

import asyncio
import requests
import time
from typing import Dict, Any, List, Optional
import statistics

from agents.base_agent import BaseAgent
from logging_config import logger

# Seuils de corrélation
CORR_HIGH_THRESHOLD  = 0.80   # > 0.80 = forte corrélation → réduction
CORR_VERY_HIGH       = 0.90   # > 0.90 = corrélation extrême → fort veto taille
CORR_ACCEPTABLE      = 0.60   # < 0.60 = corrélation acceptable

# Réduction de taille selon corrélation
REDUCTION_HIGH       = 0.40   # -40% si corrélation > 0.80
REDUCTION_VERY_HIGH  = 0.65   # -65% si corrélation > 0.90

# Cache prix
CACHE_TTL = 120.0   # 2 min

BINANCE_BASE = "https://api.binance.com"


class CorrelationWatcherAgent(BaseAgent):
    """
    Calcule la corrélation des retours entre les positions ouvertes.
    Réduit la taille des nouvelles entrées quand le portefeuille est trop corrélé.
    """

    def __init__(self):
        super().__init__(
            name="correlation_watcher",
            role=(
                "Surveillance corrélation inter-positions — "
                "réduit taille si portefeuille trop corrélé (> 0.80)"
            )
        )
        self._price_cache: Dict[str, List[float]]  = {}
        self._cache_ts:    Dict[str, float]         = {}

    # ── Domaine ────────────────────────────────────────────────────────────
    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        return any(kw in q for kw in [
            "correlation", "corrélation", "diversification",
            "concentration", "positions", "correlation_watcher",
        ]) or super()._is_in_my_domain(question)

    # ── Fetch prix (pour calculer retours) ─────────────────────────────────
    def _fetch_recent_returns(self, symbol: str, n_periods: int = 24) -> Optional[List[float]]:
        """Récupère les n dernières bougies 1h et calcule les retours."""
        now = time.time()
        sym = symbol.upper()
        if not sym.endswith("USDT"):
            sym += "USDT"

        if sym in self._price_cache and now - self._cache_ts.get(sym, 0) < CACHE_TTL:
            return self._price_cache[sym]

        try:
            url    = f"{BINANCE_BASE}/api/v3/klines"
            params = {"symbol": sym, "interval": "1h", "limit": n_periods + 1}
            resp   = requests.get(url, params=params, timeout=6)
            if resp.status_code != 200:
                return None
            klines = resp.json()
            closes = [float(k[4]) for k in klines]
            if len(closes) < 2:
                return None
            returns = [
                (closes[i] - closes[i - 1]) / closes[i - 1]
                for i in range(1, len(closes))
            ]
            self._price_cache[sym] = returns
            self._cache_ts[sym]    = now
            return returns
        except Exception as e:
            logger.warning(f"[CORR_WATCHER] Fetch error {sym}: {e}")
            return None

    # ── Calcul corrélation de Pearson ───────────────────────────────────────
    @staticmethod
    def _pearson_correlation(x: List[float], y: List[float]) -> float:
        """Calcul manuel de la corrélation de Pearson (sans numpy)."""
        n = min(len(x), len(y))
        if n < 5:
            return 0.0
        x, y = x[:n], y[:n]
        mean_x = sum(x) / n
        mean_y = sum(y) / n
        num    = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
        den_x  = (sum((xi - mean_x) ** 2 for xi in x)) ** 0.5
        den_y  = (sum((yi - mean_y) ** 2 for yi in y)) ** 0.5
        if den_x * den_y == 0:
            return 0.0
        return num / (den_x * den_y)

    # ── Calcul corrélation du portefeuille ──────────────────────────────────
    def _compute_portfolio_correlation(
        self, open_positions: List[dict], new_symbol: str
    ) -> dict:
        """
        Calcule la corrélation moyenne entre les positions existantes + le nouveau symbole.
        """
        if not open_positions:
            return {"avg_corr": 0.0, "max_corr": 0.0, "pairs": [], "new_symbol_corr": 0.0}

        new_returns = self._fetch_recent_returns(new_symbol)
        if not new_returns:
            return {"avg_corr": 0.0, "max_corr": 0.0, "pairs": [], "new_symbol_corr": 0.0}

        correlations     = []
        new_symbol_corrs = []
        pairs            = []

        existing_symbols = [
            p.get("symbol", "").replace("/", "").upper()
            for p in open_positions
            if p.get("symbol")
        ]

        for sym in existing_symbols:
            if sym == new_symbol.upper().replace("USDT", "") + "USDT":
                continue  # Même symbole, skip
            existing_returns = self._fetch_recent_returns(sym)
            if not existing_returns:
                continue
            corr = self._pearson_correlation(existing_returns, new_returns)
            new_symbol_corrs.append(corr)
            pairs.append({"pair": f"{sym}/{new_symbol}", "corr": round(corr, 3)})

        # Corrélations intra-portefeuille
        for i, sym_a in enumerate(existing_symbols):
            for sym_b in existing_symbols[i + 1:]:
                ret_a = self._fetch_recent_returns(sym_a)
                ret_b = self._fetch_recent_returns(sym_b)
                if ret_a and ret_b:
                    corr = self._pearson_correlation(ret_a, ret_b)
                    correlations.append(corr)
                    pairs.append({"pair": f"{sym_a}/{sym_b}", "corr": round(corr, 3)})

        all_corrs = correlations + new_symbol_corrs
        avg_corr  = statistics.mean(all_corrs) if all_corrs else 0.0
        max_corr  = max(new_symbol_corrs) if new_symbol_corrs else 0.0

        return {
            "avg_corr":        round(avg_corr, 3),
            "max_corr":        round(max_corr, 3),
            "new_symbol_corr": round(statistics.mean(new_symbol_corrs) if new_symbol_corrs else 0.0, 3),
            "pairs":           sorted(pairs, key=lambda p: -abs(p["corr"]))[:5],
        }

    # ── Respond ─────────────────────────────────────────────────────────────
    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        symbol         = context.get("symbol", "BTCUSDT")
        open_positions = context.get("open_positions_detail", [])
        n_positions    = len(open_positions) if isinstance(open_positions, list) else 0

        if n_positions == 0:
            return {
                "agent":          self.name,
                "summary":        "✅ Aucune position ouverte — corrélation N/A",
                "confidence":     0.5,
                "recommendation": "TRADE AUTORISÉ — portefeuille vide",
                "avg_corr":       0.0,
                "size_reduction": 0.0,
            }

        loop = asyncio.get_event_loop()
        corr_data = await loop.run_in_executor(
            None,
            lambda: self._compute_portfolio_correlation(open_positions, symbol)
        )

        avg_corr  = corr_data["avg_corr"]
        max_corr  = corr_data["max_corr"]
        new_corr  = corr_data["new_symbol_corr"]
        pairs     = corr_data["pairs"]

        if new_corr >= CORR_VERY_HIGH:
            size_reduction = REDUCTION_VERY_HIGH
            rec = (
                f"TAILLE RÉDUITE -{int(size_reduction*100)}% — "
                f"Corrélation très élevée avec portefeuille ({new_corr:.0%})"
            )
            confidence = 0.85
            summary = f"🔗 Corrélation extrême {symbol}/{n_positions} positions: {new_corr:.0%} → taille -{int(size_reduction*100)}%"

        elif new_corr >= CORR_HIGH_THRESHOLD:
            size_reduction = REDUCTION_HIGH
            rec = (
                f"TAILLE RÉDUITE -{int(size_reduction*100)}% — "
                f"Corrélation forte avec portefeuille ({new_corr:.0%})"
            )
            confidence = 0.72
            summary = f"⚠️ Corrélation forte {symbol}/{n_positions} positions: {new_corr:.0%} → taille -{int(size_reduction*100)}%"

        else:
            size_reduction = 0.0
            rec = f"TAILLE NORMALE — Corrélation acceptable ({new_corr:.0%})"
            confidence = 0.6
            summary = f"✅ Corrélation OK {symbol}: {new_corr:.0%} — diversification acceptable"

        pair_text = [f"{p['pair']}: {p['corr']:.0%}" for p in pairs[:3]]

        return {
            "agent":          self.name,
            "summary":        summary,
            "arguments":      [
                f"Corrélation {symbol}/portefeuille: {new_corr:.0%}",
                f"Corrélation moyenne intra-portefeuille: {avg_corr:.0%}",
                f"Positions ouvertes: {n_positions}",
            ] + pair_text,
            "risks":          ["Concentration excessive" if new_corr > CORR_HIGH_THRESHOLD else ""],
            "confidence":     confidence,
            "recommendation": rec,
            "avg_corr":       avg_corr,
            "max_corr":       max_corr,
            "new_symbol_corr": new_corr,
            "size_reduction": size_reduction,
            "pairs":          pairs,
        }
