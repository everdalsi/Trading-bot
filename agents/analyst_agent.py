"""
📊 ANALYST AGENT V5 — Expert en Analyse Technique + Divergences + Multi-Timeframe
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AMÉLIORATIONS V5 :
- Calcul RSI divergences haussières/baissières (indicateur en or ignoré en V4)
- Bollinger Bands : squeeze + breakout (momentum explosion imminent)
- MACD : histogramme direction + crossover signal
- Volume confirmation : volume > SMA20 volume = signal fort
- Structure de marché : HH/HL (uptrend) vs LH/LL (downtrend)
- Score composite pondéré 8 facteurs (était juste lecture WR context)
- Cache Binance 60s par symbole pour vitesse
"""

import time
import requests
import numpy as np
from typing import Dict, Any, List, Tuple
from agents.base_agent import BaseAgent
from logging_config import logger


BINANCE_BASE = "https://api.binance.com"


class AnalystAgent(BaseAgent):

    def __init__(self):
        super().__init__(
            name="analyst",
            description="Analyse technique avancée : indicateurs, divergences, structure de marché, confirmations volume",
            role="Analyse technique experte — RSI, MACD, BB, Volume, Divergences, Structure de marché"
        )
        self._klines_cache: Dict[str, Dict] = {}
        self._cache_ttl = 60

    # ────────────────────────────────────────────────────────────────────────
    # COLLECTE DONNÉES RÉELLES
    # ────────────────────────────────────────────────────────────────────────

    def _get_klines(self, symbol: str, interval: str = "5m", limit: int = 100) -> Tuple[List[float], List[float], List[float], List[float]]:
        """Retourne (closes, highs, lows, volumes) depuis Binance avec cache 60s."""
        key = f"{symbol}_{interval}"
        now = time.time()
        cached = self._klines_cache.get(key)
        if cached and now - cached["ts"] < self._cache_ttl:
            return cached["data"]

        # Essai DataHandler en premier
        try:
            from data_handler import DataHandler
            dh = DataHandler()
            closes = list(dh.get_klines(symbol, interval, limit))
            if closes and len(closes) >= 20:
                # Fallback: highs/lows/volumes approximés si DataHandler ne les retourne pas
                result = (closes, closes, closes, [0.0] * len(closes))
                self._klines_cache[key] = {"data": result, "ts": now}
                return result
        except Exception:
            pass

        try:
            r = requests.get(
                f"{BINANCE_BASE}/api/v3/klines",
                params={"symbol": symbol.upper(), "interval": interval, "limit": limit},
                timeout=8
            )
            if r.status_code == 200:
                raw = r.json()
                closes  = [float(c[4]) for c in raw]
                highs   = [float(c[2]) for c in raw]
                lows    = [float(c[3]) for c in raw]
                volumes = [float(c[5]) for c in raw]
                result = (closes, highs, lows, volumes)
                self._klines_cache[key] = {"data": result, "ts": now}
                return result
        except Exception as e:
            logger.warning(f"[AnalystAgent] klines {symbol}: {e}")

        return ([], [], [], [])

    # ────────────────────────────────────────────────────────────────────────
    # INDICATEURS TECHNIQUES
    # ────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _rsi(closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i - 1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - 100 / (1 + rs), 2)

    @staticmethod
    def _rsi_series(closes: List[float], period: int = 14) -> List[float]:
        """Série RSI complète pour calcul divergence."""
        if len(closes) < period + 2:
            return [50.0] * len(closes)
        result = [50.0] * period
        gains = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
        avg_g = np.mean(gains[:period])
        avg_l = np.mean(losses[:period])
        for i in range(period, len(closes) - 1):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
            rs = avg_g / avg_l if avg_l > 0 else 100
            result.append(round(100 - 100 / (1 + rs), 2))
        return result

    @staticmethod
    def _macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, float]:
        """MACD line, Signal line, Histogramme."""
        if len(closes) < slow + signal:
            return {"macd": 0.0, "signal": 0.0, "hist": 0.0, "cross": "NONE"}
        ema = lambda data, n: pd.Series_ema(data, n)
        # Calcul EMA manuel
        def calc_ema(data, n):
            k = 2 / (n + 1)
            ema_val = data[0]
            result = [ema_val]
            for v in data[1:]:
                ema_val = v * k + ema_val * (1 - k)
                result.append(ema_val)
            return result
        ema_fast   = calc_ema(closes, fast)
        ema_slow   = calc_ema(closes, slow)
        macd_line  = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]
        sig_line   = calc_ema(macd_line[-signal*3:], signal)
        hist       = macd_line[-1] - sig_line[-1]
        # Crossover : macd_line[-1] vs [-2] relative to signal
        cross = "NONE"
        if len(macd_line) >= 2 and len(sig_line) >= 2:
            if macd_line[-2] < sig_line[-2] and macd_line[-1] > sig_line[-1]:
                cross = "BULL_CROSS"
            elif macd_line[-2] > sig_line[-2] and macd_line[-1] < sig_line[-1]:
                cross = "BEAR_CROSS"
        return {
            "macd":   round(macd_line[-1], 6),
            "signal": round(sig_line[-1], 6),
            "hist":   round(hist, 6),
            "cross":  cross
        }

    @staticmethod
    def _bollinger_bands(closes: List[float], period: int = 20, k: float = 2.0) -> Dict[str, float]:
        if len(closes) < period:
            mid = closes[-1] if closes else 0
            return {"upper": mid * 1.02, "mid": mid, "lower": mid * 0.98,
                    "width": 0.04, "pct_b": 0.5, "squeeze": False}
        window = closes[-period:]
        mid    = np.mean(window)
        std    = np.std(window, ddof=1)
        upper  = mid + k * std
        lower  = mid - k * std
        width  = (upper - lower) / mid if mid > 0 else 0
        price  = closes[-1]
        pct_b  = (price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
        squeeze = width < 0.03   # squeeze si bandes très serrées
        return {
            "upper": round(upper, 4), "mid": round(mid, 4), "lower": round(lower, 4),
            "width": round(width, 4), "pct_b": round(pct_b, 3), "squeeze": squeeze
        }

    @staticmethod
    def _detect_divergence(closes: List[float], rsi_series: List[float], lookback: int = 20) -> str:
        """
        Divergences RSI — le signal le plus fiable en trading :
        - Haussière : prix fait lower low MAIS RSI fait higher low → retournement probable
        - Baissière : prix fait higher high MAIS RSI fait lower high → sommet probable
        """
        if len(closes) < lookback or len(rsi_series) < lookback:
            return "NONE"
        p  = closes[-lookback:]
        r  = rsi_series[-lookback:]
        # Recherche pivot bas (creux)
        price_lower_low = p[-1] < min(p[:-1])
        rsi_higher_low  = r[-1] > min(r[:-1])
        # Recherche pivot haut (sommet)
        price_higher_high = p[-1] > max(p[:-1])
        rsi_lower_high    = r[-1] < max(r[:-1])

        if price_lower_low and rsi_higher_low:
            return "BULL_DIVERGENCE"    # 🟢 signal achat fort
        elif price_higher_high and rsi_lower_high:
            return "BEAR_DIVERGENCE"    # 🔴 signal vente fort
        return "NONE"

    @staticmethod
    def _market_structure(highs: List[float], lows: List[float], n: int = 5) -> str:
        """Structure de marché : HH+HL = uptrend, LH+LL = downtrend."""
        if len(highs) < n * 2 or len(lows) < n * 2:
            return "UNDEFINED"
        recent_h = highs[-n:]
        prev_h   = highs[-n*2:-n]
        recent_l = lows[-n:]
        prev_l   = lows[-n*2:-n]
        hh = max(recent_h) > max(prev_h)
        hl = min(recent_l) > min(prev_l)
        lh = max(recent_h) < max(prev_h)
        ll = min(recent_l) < min(prev_l)
        if hh and hl:
            return "UPTREND"
        elif lh and ll:
            return "DOWNTREND"
        elif not hh and not ll:
            return "RANGE"
        return "TRANSITION"

    @staticmethod
    def _volume_score(volumes: List[float], period: int = 20) -> float:
        """Score volume : > 1.5x SMA = conviction forte."""
        if len(volumes) < period or volumes[-1] == 0:
            return 0.5
        sma_vol = np.mean(volumes[-period:])
        return min(volumes[-1] / sma_vol, 3.0) / 3.0 if sma_vol > 0 else 0.5

    @staticmethod
    def _atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        """Average True Range — mesure la volatilité réelle."""
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            trs.append(tr)
        return round(np.mean(trs[-period:]), 6)

    # ────────────────────────────────────────────────────────────────────────
    # SCORE COMPOSITE
    # ────────────────────────────────────────────────────────────────────────

    def _compute_technical_score(
        self, closes: List[float], highs: List[float], lows: List[float], volumes: List[float]
    ) -> Dict[str, Any]:
        """
        Score composite [-1, +1] pondéré par 8 signaux techniques.
        Positif = bullish, négatif = bearish.
        """
        if not closes or len(closes) < 26:
            return {"score": 0.0, "signals": {}, "confidence": 0.3}

        rsi_val  = self._rsi(closes)
        rsi_ser  = self._rsi_series(closes)
        macd_d   = self._macd(closes)
        bb       = self._bollinger_bands(closes)
        div      = self._detect_divergence(closes, rsi_ser)
        struct   = self._market_structure(highs, lows)
        vol_sc   = self._volume_score(volumes)
        atr      = self._atr(highs, lows, closes)

        # 1. RSI [-1, +1]
        rsi_signal = (rsi_val - 50) / 50.0

        # 2. MACD histogram
        macd_signal = 1.0 if macd_d["cross"] == "BULL_CROSS" else \
                      -1.0 if macd_d["cross"] == "BEAR_CROSS" else \
                      (1.0 if macd_d["hist"] > 0 else -1.0) * min(abs(macd_d["hist"]) * 1000, 1.0)

        # 3. Bollinger position
        bb_signal = (bb["pct_b"] - 0.5) * 2   # 0 = lower band, 1 = upper band → [-1, +1]
        if bb["squeeze"]:
            bb_signal *= 1.3  # boost squeeze (breakout imminent)

        # 4. Divergence (très fort signal)
        div_signal = 1.0 if div == "BULL_DIVERGENCE" else -1.0 if div == "BEAR_DIVERGENCE" else 0.0

        # 5. Structure de marché
        struct_signal = 1.0 if struct == "UPTREND" else -1.0 if struct == "DOWNTREND" else 0.0

        # 6. Volume (amplificateur ≠ directionnel)
        vol_boost = 0.8 + vol_sc * 0.4  # [0.8, 1.2]

        # 7. Momentum (variation 5 dernières bougies)
        momentum = (closes[-1] - closes[-6]) / closes[-6] * 10 if len(closes) >= 6 and closes[-6] > 0 else 0
        momentum = max(-1.0, min(1.0, momentum))

        # Score composite pondéré
        raw_score = (
            0.22 * rsi_signal +
            0.18 * macd_signal +
            0.15 * bb_signal +
            0.20 * div_signal +       # divergence = signal premium
            0.15 * struct_signal +
            0.10 * momentum
        ) * vol_boost

        score = max(-1.0, min(1.0, raw_score))

        # Confiance basée sur convergence des signaux
        signals_dir = [rsi_signal, macd_signal, struct_signal, momentum]
        positive = sum(1 for s in signals_dir if s > 0.1)
        negative = sum(1 for s in signals_dir if s < -0.1)
        convergence = abs(positive - negative) / len(signals_dir)
        confidence = 0.50 + convergence * 0.40

        return {
            "score":     round(score, 3),
            "confidence": round(confidence, 2),
            "rsi":        rsi_val,
            "macd":       macd_d,
            "bb":         bb,
            "divergence": div,
            "structure":  struct,
            "volume_score": round(vol_sc, 2),
            "atr":        atr,
            "momentum":   round(momentum, 3),
            "signals": {
                "rsi":       round(rsi_signal, 2),
                "macd":      round(macd_signal, 2),
                "bollinger": round(bb_signal, 2),
                "divergence": div_signal,
                "structure": struct_signal,
                "momentum":  round(momentum, 2),
                "volume_boost": round(vol_boost, 2),
            }
        }

    # ────────────────────────────────────────────────────────────────────────
    # PERFORMANCE STATS (depuis context)
    # ────────────────────────────────────────────────────────────────────────

    def _read_context_stats(self, context: dict) -> Dict[str, Any]:
        wr_live     = context.get("wr_live")
        wins_live   = context.get("wins_live")
        losses_live = context.get("losses_live")
        total_live  = context.get("total_trades")
        sharpe_live = context.get("sharpe")
        pf_live     = context.get("profit_factor")
        return {
            "wr_live": wr_live, "wins": wins_live, "losses": losses_live,
            "total": total_live, "sharpe": sharpe_live, "pf": pf_live
        }

    # ────────────────────────────────────────────────────────────────────────
    # RÉPONSE PRINCIPALE
    # ────────────────────────────────────────────────────────────────────────

    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        keywords = [
            "analyse", "analyst", "technique", "rsi", "macd", "bollinger",
            "performance", "winrate", "stat", "score", "synthèse", "débat",
            "raffine", "cerveau collectif", "trade ou no trade", "analyse collective",
        ]
        return any(kw in q for kw in keywords)

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {
                "agent": self.name, "summary": "⚠️ Hors domaine analyst",
                "confidence": 0.0, "recommendation": "HOLD"
            }

        symbol = context.get("symbol", "BTCUSDT")
        shared_glossary = context.get("shared_glossary", {})

        # ── Analyse technique réelle ─────────────────────────────────────
        closes, highs, lows, volumes = self._get_klines(symbol, "5m", 100)

        # Multi-timeframe : récupérer aussi le 1h pour structure HTF
        closes_1h, highs_1h, lows_1h, _ = self._get_klines(symbol, "1h", 50)

        tech = self._compute_technical_score(closes, highs, lows, volumes)
        tech_1h = self._compute_technical_score(closes_1h, highs_1h, lows_1h, [])

        score = tech["score"]
        # Alignement multi-timeframe bonus
        if tech_1h["score"] * score > 0:
            score = score * 1.1   # même direction 5m + 1h = +10%

        score = max(-1.0, min(1.0, score))

        # ── Stats performance depuis contexte ───────────────────────────
        stats = self._read_context_stats(context)
        wr = stats["wr_live"]

        # ── Décision ────────────────────────────────────────────────────
        if score > 0.35:
            reco = "BUY"
            summary_direction = "🟢 Signal BULLISH technique confirmé"
        elif score < -0.35:
            reco = "SELL"
            summary_direction = "🔴 Signal BEARISH technique détecté"
        else:
            reco = "HOLD"
            summary_direction = "🟡 Signal NEUTRE — attendre confirmation"

        # Divergence override
        if tech["divergence"] == "BULL_DIVERGENCE":
            summary_direction += " | 🔔 DIVERGENCE HAUSSIÈRE RSI détectée (signal fort)"
        elif tech["divergence"] == "BEAR_DIVERGENCE":
            summary_direction += " | ⚠️ DIVERGENCE BAISSIÈRE RSI détectée (signal fort)"

        # Squeeze Bollinger
        if tech["bb"]["squeeze"]:
            summary_direction += " | 💥 SQUEEZE BB — explosion imminent"

        arguments = [
            f"RSI 5m: {tech['rsi']:.1f} | Structure: {tech['structure']} | Divergence: {tech['divergence']}",
            f"MACD histogramme: {tech['macd']['hist']:+.6f} | Cross: {tech['macd']['cross']}",
            f"Bollinger %B: {tech['bb']['pct_b']:.2f} | Squeeze: {'OUI' if tech['bb']['squeeze'] else 'non'} | Bandes: {tech['bb']['width']:.3%}",
            f"Volume score: {tech['volume_score']:.2f}x SMA20 | ATR: {tech['atr']:.4f}",
            f"Momentum 5 bougies: {tech['momentum']:+.3f} | Score 1h: {tech_1h['score']:+.3f}",
        ]
        if isinstance(wr, (int, float)):
            arguments.append(f"WR live: {wr:.1f}% sur {stats['total'] or '?'} trades")

        full_summary = (
            f"📊 Analyst V5 — {symbol} | Score technique: {score:+.3f} | "
            f"RSI: {tech['rsi']:.1f} | Div: {tech['divergence']} | "
            f"BB squeeze: {tech['bb']['squeeze']} | Struct: {tech['structure']} | {summary_direction}"
        )

        confidence = tech["confidence"]
        if tech["divergence"] != "NONE":
            confidence = min(0.95, confidence + 0.10)

        return {
            "agent":         self.name,
            "summary":       full_summary,
            "arguments":     arguments,
            "score":         score,
            "rsi":           tech["rsi"],
            "macd":          tech["macd"],
            "bollinger":     tech["bb"],
            "divergence":    tech["divergence"],
            "structure":     tech["structure"],
            "atr":           tech["atr"],
            "volume_score":  tech["volume_score"],
            "signals":       tech["signals"],
            "confidence":    confidence,
            "recommendation": reco,
            "full_summary":  full_summary,
            "glossary_used": True,
        }
