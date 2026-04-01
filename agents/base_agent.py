"""
🧠 BASE AGENT V4.0 — Cerveau commun + Personnalités OHMO.AI + Spécialisation
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX V3.2 :
- domain_keywords complété : supervisor, wallet_copier, social_listener,
  portfolio_manager, knowledge_specialist, hedging, self_improvement
  → plus aucun agent exclu du débat collectif par défaut
- Mots-clés "débat collectif" ajoutés à TOUS les agents via default_debate_keywords
- Timeout safe_respond étendu à 10s (8s trop court pour certaines analyses)

UPGRADE V4.0 — Système de personnalités (inspiré OHMO.AI) :
- RETAIL       : panic buy/sell, FOMO, sur-réaction aux news
- INSTITUTIONAL: fade moves, patience, contre-tendance, smart money
- LEADER       : attente de confirmation, haute conviction requise
Chaque personnalité biaise légèrement confidence + comportement rapporté.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any
from knowledge_base import KnowledgeBase
import asyncio


# ======================== PATCH SINGLETON KB V4.1 ========================
class _KnowledgeBaseSingleton:
    _instance = None

    @staticmethod
    def get_instance():
        if _KnowledgeBaseSingleton._instance is None:
            _KnowledgeBaseSingleton._instance = KnowledgeBase()
        return _KnowledgeBaseSingleton._instance
# =========================================================================


# ======================== SYSTÈME DE PERSONNALITÉS V4.0 ==================
PERSONALITY_RETAIL        = "RETAIL"
PERSONALITY_INSTITUTIONAL = "INSTITUTIONAL"
PERSONALITY_LEADER        = "LEADER"

# Mapping agent → personnalité (comportement naturel de chaque profil)
AGENT_PERSONALITY_MAP: Dict[str, str] = {
    # RETAIL : réactif aux news, sentiment, FOMO, sur-réaction
    "social_listener":      PERSONALITY_RETAIL,
    "fear_greed":           PERSONALITY_RETAIL,
    "news_event":           PERSONALITY_RETAIL,
    "sentiment_aggregator": PERSONALITY_RETAIL,
    "polymarket_arb":       PERSONALITY_RETAIL,
    "event_sniper":         PERSONALITY_RETAIL,
    "sports_arb":           PERSONALITY_RETAIL,

    # INSTITUTIONAL : smart money, patience, fade the move, contre-tendanciel
    "risk":                 PERSONALITY_INSTITUTIONAL,
    "hedging":              PERSONALITY_INSTITUTIONAL,
    "correlation_watcher":  PERSONALITY_INSTITUTIONAL,
    "wallet_copier":        PERSONALITY_INSTITUTIONAL,
    "whale_tracker":        PERSONALITY_INSTITUTIONAL,
    "drawdown_guard":       PERSONALITY_INSTITUTIONAL,
    "derivatives":          PERSONALITY_INSTITUTIONAL,
    "exchange_flow":        PERSONALITY_INSTITUTIONAL,
    "cross_asset":          PERSONALITY_INSTITUTIONAL,
    "regulatory_monitor":   PERSONALITY_INSTITUTIONAL,
    "on_chain":             PERSONALITY_INSTITUTIONAL,
    "blockchain_health":    PERSONALITY_INSTITUTIONAL,
    "token_unlock":         PERSONALITY_INSTITUTIONAL,
    "defi_monitor":         PERSONALITY_INSTITUTIONAL,

    # LEADER : attente de confirmation, haute conviction, décision finale
    "analyst":              PERSONALITY_LEADER,
    "trader":               PERSONALITY_LEADER,
    "supervisor":           PERSONALITY_LEADER,
    "quant_ml":             PERSONALITY_LEADER,
    "regime_detector":      PERSONALITY_LEADER,
    "macro_regime":         PERSONALITY_LEADER,
    "quantum_risk":         PERSONALITY_LEADER,
    "vol_regime":           PERSONALITY_LEADER,
    "portfolio_manager":    PERSONALITY_LEADER,
    "pattern_recognition":  PERSONALITY_LEADER,
    "macro_calendar":       PERSONALITY_LEADER,
    "arbitrage_scanner":    PERSONALITY_LEADER,
    "options_flow":         PERSONALITY_LEADER,
    "grid_strategy":        PERSONALITY_LEADER,
    "liquidation_tracker":  PERSONALITY_LEADER,
    "scenario_injector":    PERSONALITY_LEADER,
}

# Profils de comportement par personnalité
# FIX V5.0 TRAINING: seuils abaissés pour maximiser les trades en mode apprentissage
_TRAINING_MODE = __import__('os').environ.get("BOT_TRAINING_MODE", "True").lower() in ("true", "1", "yes")

PERSONALITY_PROFILES: Dict[str, Dict] = {
    PERSONALITY_RETAIL: {
        "label":            "🔴 RETAIL",
        "confidence_boost": +0.10,   # TRAINING FIX: boost amplifié pour signaler davantage
        "min_threshold":    0.05,    # TRAINING FIX: seuil très bas → signale presque toujours
        "behavior":         "panic_buy_sell",
        "description":      "Réactif aux news, FOMO, sur-réaction aux tendances",
    },
    PERSONALITY_INSTITUTIONAL: {
        "label":            "🔵 INSTITUTIONAL",
        "confidence_boost": 0.0,     # TRAINING FIX: neutre au lieu de négatif
        "min_threshold":    0.08,    # TRAINING FIX: seuil abaissé (était 0.55)
        "behavior":         "fade_moves",
        "description":      "Smart money, patient, contre-tendanciel",
    },
    PERSONALITY_LEADER: {
        "label":            "🟡 LEADER",
        "confidence_boost": +0.05,   # TRAINING FIX: léger boost positif
        "min_threshold":    0.10,    # TRAINING FIX: seuil abaissé (était 0.60)
        "behavior":         "wait_confirm",
        "description":      "Attente de confirmation, haute conviction requise",
    },
}
# =========================================================================


# Mots-clés de débat collectif → tous les agents y participent
_DEBATE_KEYWORDS = [
    # Orchestrateur → tous les agents participent TOUJOURS à toute question de trading
    "signal", "analyse", "trading", "trade",  # question standard: "analyse trading signal SYMBOL"
    "synthèse", "synthétise", "débat", "cerveau collectif",
    "final decision", "raffine", "trade ou no trade",
    "décision finale", "orchestrator", "ask_all", "round",
    "micro", "analyse collective",
]


class BaseAgent(ABC):
    """
    BASE AGENT V4.0 — Cerveau commun + Personnalités OHMO.AI + Spécialisation
    """

    def __init__(self, name: str, role: str = None, description: str = None):
        self.name        = name
        self.role        = role or description
        self.description = description or role
        self.kb          = _KnowledgeBaseSingleton.get_instance()
        self._bg_signal   = {}   # Cache pré-analyse background
        self._bg_insights = []   # Insights auto-amélioration
        self._bg_cycle    = 0    # Compteur cycles background

        # V4.0 : Personnalité assignée automatiquement selon le nom de l'agent
        _p_key                   = AGENT_PERSONALITY_MAP.get(name, PERSONALITY_INSTITUTIONAL)
        self.personality         = _p_key
        self.personality_profile = PERSONALITY_PROFILES[_p_key]


    def bg_tick(self, ctx: dict, cycle_id: int) -> dict:
        """
        Travail en arrière-plan pendant HOLD.
        cycle pair   → pré-analyse (calcul signaux techniques sans LLM)
        cycle impair → auto-amélioration (révise perf passée, ajuste seuils)
        Retourne {"type","signal","insight"} — stocké dans _bg_signal/_bg_insights.
        """
        import json as _json, os as _os, time as _time
        self._bg_cycle += 1
        try:
            if cycle_id % 2 == 0:
                # ── CAS PAIR : pré-analyse technique ─────────────────────────────
                closes = ctx.get("closes_5m", ctx.get("closes", []))
                if len(closes) >= 14:
                    rsi  = self.tools.rsi(closes)
                    ema9 = self.tools.ema(closes, 9)
                    ema21= self.tools.ema(closes, 21)
                    trend= self.tools.trend_strength(closes)
                    sig  = "BUY" if (rsi < 40 and ema9 > ema21) else \
                           "SELL" if (rsi > 65 and ema9 < ema21) else "HOLD"
                    self._bg_signal = {
                        "agent": self.name, "signal": sig,
                        "rsi": round(rsi, 1), "trend": trend["direction"],
                        "slope": trend["slope_pct"], "ts": int(_time.time())
                    }
                return {"type": "preanalysis", "signal": self._bg_signal}
            else:
                # ── CAS IMPAIR : auto-amélioration ───────────────────────────────
                perf_file = "/tmp/agent_perf.json"
                insight = {"agent": self.name, "type": "self_improve", "ts": int(_time.time())}
                if _os.path.exists(perf_file):
                    with open(perf_file) as _f: perfs = _json.load(_f)
                    key = self.name.lower().replace(" ", "_").replace("-", "_")
                    p = perfs.get(key, {})
                    total, wins = p.get("total", 0), p.get("wins", 0)
                    if total >= 3:
                        wr = wins / total
                        # Auto-ajustement du seuil de confiance minimal
                        if wr < 0.35 and not hasattr(self, "_conf_penalty"):
                            self._conf_penalty = 0.05
                            insight["action"] = f"WR={wr:.0%} → +5% seuil confiance"
                        elif wr > 0.60 and hasattr(self, "_conf_penalty"):
                            del self._conf_penalty
                            insight["action"] = f"WR={wr:.0%} → seuil confiance rétabli"
                        else:
                            insight["action"] = f"WR={wr:.0%} ({total} trades) — stable"
                        insight["win_rate"] = round(wr, 3)
                self._bg_insights.append(insight)
                self._bg_insights = self._bg_insights[-20:]  # Max 20 insights
                return {"type": "self_improve", "insight": insight}
        except Exception as _e:
            return {"type": "error", "agent": self.name, "err": str(_e)}

    @abstractmethod
    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Chaque agent doit retourner EXACTEMENT ce format."""
        pass

    def _apply_personality_bias(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        V4.0 : Applique le biais de personnalité sur la confiance du résultat.
        - RETAIL      : +0.05 confiance (sur-réactif, amplifie signal)
        - INSTITUTIONAL: -0.05 confiance (conservateur, fade the move)
        - LEADER      : neutre, exige haute conviction externe
        Ajoute les champs 'personality' et 'behavior_type' à la réponse.
        """
        profile   = self.personality_profile
        raw_conf  = result.get("confidence", 0.0)
        new_conf  = max(0.0, min(1.0, raw_conf + profile["confidence_boost"]))

        result["confidence"]    = new_conf
        result["personality"]   = profile["label"]
        result["behavior_type"] = profile["behavior"]
        return result

    async def safe_respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Version ultra-sécurisée avec timeout + garde-fou exception + biais personnalité."""
        try:
            result = await asyncio.wait_for(
                self.respond(question, context),
                timeout=10.0
            )
            required_keys = {"agent", "summary", "confidence", "recommendation"}
            if not all(k in result for k in required_keys):
                result = {
                    "agent":          self.name,
                    "summary":        f"⚠️ Format invalide par {self.name} → corrigé",
                    "confidence":     0.0,
                    "recommendation": "Vérifier rôle",
                    "error":          "missing_keys",
                }
            if not self._is_in_my_domain(question):
                result["warning"] = (
                    f"{self.name} a répondu hors de sa spécialité → ignoré par orchestreur"
                )
            # V4.0 : biais de personnalité appliqué sur chaque réponse valide
            result = self._apply_personality_bias(result)
            return result

        except asyncio.TimeoutError:
            return {
                "agent":          self.name,
                "summary":        f"Timeout dans {self.name} (10s)",
                "confidence":     0.0,
                "recommendation": "HOLD - Timeout",
                "error":          "timeout",
                "personality":    self.personality_profile["label"],
                "behavior_type":  self.personality_profile["behavior"],
            }
        except Exception as e:
            return {
                "agent":          self.name,
                "summary":        f"Erreur interne {self.name}: {str(e)[:80]}",
                "confidence":     0.0,
                "recommendation": "Vérifier logs",
                "risks":          ["Exception"],
                "personality":    self.personality_profile["label"],
                "behavior_type":  self.personality_profile["behavior"],
            }

    def _is_in_my_domain(self, question: str) -> bool:
        """
        FIX V3.2 : domain_keywords complété pour TOUS les agents.
        Chaque agent peut aussi participer au débat collectif via _DEBATE_KEYWORDS.
        """
        domain_keywords = {
            # ── Agents cœur ──────────────────────────────────────────────────
            "trader": [
                "buy", "sell", "hold", "trade", "position",
                "décision", "entry", "exit", "long", "short", "ordre",
            ],
            "risk": [
                "risk", "drawdown", "kelly", "veto", "perte", "stop",
                "position", "liquidation", "levier", "sizing",
            ],
            "analyst": [
                "pattern", "wyckoff", "vsa", "technique", "analyse",
                "indicateur", "rsi", "macd", "bollinger", "ema",
            ],
            "learning": [
                "leçon", "mistake", "amélioration", "lesson",
                "blacklist", "pattern", "historique", "mémoire",
            ],
            "research": [
                "analyse", "recherche", "kol", "on-chain", "spoofing",
                "wash", "mev", "order book", "sentiment", "klines",
                "fear greed", "whale", "liquidation", "data",
            ],
            # ── Agents spécialisés ───────────────────────────────────────────
            "quant_ml": [
                "regime", "backtest", "model", "bull", "bear",
                "sideways", "volatile", "trend", "ml", "quant", "macro",
            ],
            "execution_engine": [
                "execute", "twap", "slice", "vwap", "order", "fill",
                "slippage", "timing", "split", "exécution",
            ],
            "yield_staking": [
                "stake", "lido", "marinade", "apy", "yield",
                "staking", "rewards", "liquid", "farming",
            ],
            "hedging": [
                "hedge", "couverture", "protection", "short",
                "options", "futures", "delta", "neutral",
            ],
            "portfolio_manager": [
                "portfolio", "savings", "allocation", "rebalance",
                "diversification", "capital", "gestion",
            ],
            # ── Agents supervision / mémoire ─────────────────────────────────
            "supervisor": [
                "supervisor", "synthèse", "arbitre", "final",
                "décision finale", "vote", "consensus", "portfolio",
                "wallet", "savings", "staking", "transfer", "funding",
            ],
            "self_improvement": [
                "monitor", "health", "santé", "watchdog", "immune",
                "repair", "anomalie", "crash", "erreur système",
                "surveillance", "self_improvement",
            ],
            "evolution": [
                "évolution", "evolution", "amélioration", "upgrade",
                "améliorer", "modifier code", "auto-modif", "max trades",
                "monitor", "health", "santé", "watchdog", "immune",
            ],
            "wallet_copier": [
                "wallet", "copier", "copy", "smart money", "baleine",
                "whale wallet", "on-chain", "adresse", "follow",
            ],
            "social_listener": [
                "social", "twitter", "reddit", "sentiment", "fear",
                "greed", "news", "actualité", "trending", "buzz",
            ],
            "knowledge_specialist": [
                "knowledge", "connaissance", "pdf", "wyckoff",
                "livre", "stratégie", "méthode", "vsa", "cfa",
            ],
            "scenario_injector": [
                "scenario", "scénario", "inject", "simulate", "polymarket",
                "pre-price", "before price", "priced in", "opportunity",
            ],
            # ── Fallback ─────────────────────────────────────────────────────
            "default": list(_DEBATE_KEYWORDS),
        }

        q = question.lower()

        # 1. Vérifie si c'est une question de débat collectif → tout le monde participe
        if any(kw in q for kw in _DEBATE_KEYWORDS):
            return True

        # 2. Vérifie les keywords spécifiques de l'agent
        keywords = domain_keywords.get(self.name, domain_keywords["default"])
        return any(kw in q for kw in keywords)

    def explain_term(self, term: str) -> str:
        """Tous les agents utilisent le même glossaire → zéro malentendu."""
        try:
            explanation = self.kb.explain_term(term)
            return explanation or f"{term} (définition partagée)"
        except Exception:
            return f"{term} (glossaire indisponible)"


# ═══════════════════════════════════════════════════════════════════
# TECHNICAL TOOLS — Accessible à tous les agents via self.tools
# ═══════════════════════════════════════════════════════════════════

class TechnicalTools:
    """Boîte à outils technique partagée. Tous les agents ont self.tools = TechnicalTools()."""

    @staticmethod
    def rsi(closes: list, period: int = 14) -> float:
        """RSI Wilder. Retourne 50.0 si données insuffisantes."""
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i - 1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        ag = sum(gains[-period:]) / period
        al = sum(losses[-period:]) / period
        if al == 0:
            return 100.0
        return round(100 - 100 / (1 + ag / al), 2)

    @staticmethod
    def ema(closes: list, period: int) -> float:
        """EMA lissée standard."""
        if not closes: return 0.0
        if len(closes) < period: return closes[-1]
        k = 2 / (period + 1)
        val = sum(closes[:period]) / period
        for c in closes[period:]:
            val = c * k + val * (1 - k)
        return round(val, 6)

    @staticmethod
    def atr(highs: list, lows: list, closes: list, period: int = 14) -> float:
        """Average True Range."""
        if len(closes) < 2: return 0.0
        trs = []
        for i in range(1, min(len(highs), len(lows), len(closes))):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        if not trs: return 0.0
        return round(sum(trs[-period:]) / min(len(trs), period), 6)

    @staticmethod
    def bollinger(closes: list, period: int = 20, num_std: float = 2.0) -> dict:
        """Bandes de Bollinger. Retourne {"mid","upper","lower","pct_b"}."""
        if len(closes) < period:
            c = closes[-1] if closes else 0.0
            return {"mid": c, "upper": c, "lower": c, "pct_b": 0.5}
        w = closes[-period:]
        mid = sum(w) / period
        std = (sum((x - mid) ** 2 for x in w) / period) ** 0.5
        upper, lower = mid + num_std * std, mid - num_std * std
        pct_b = (closes[-1] - lower) / (upper - lower) if upper != lower else 0.5
        return {"mid": round(mid,6), "upper": round(upper,6), "lower": round(lower,6), "pct_b": round(pct_b,4)}

    @staticmethod
    def macd(closes: list, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
        """MACD standard. Retourne {"macd","signal","hist"}."""
        if len(closes) < slow: return {"macd": 0.0, "signal": 0.0, "hist": 0.0}
        def _e(d, p):
            k = 2 / (p + 1); v = sum(d[:p]) / p
            for x in d[p:]: v = x * k + v * (1 - k)
            return v
        m = _e(closes, fast) - _e(closes, slow)
        # Signal via mini-series
        ms = [_e(closes[:i], fast) - _e(closes[:i], slow)
                  for i in range(slow, len(closes), max(1, len(closes)//slow))]
        ms.append(m)
        sig_line = _e(ms, min(signal, len(ms)))
        return {"macd": round(m,6), "signal": round(sig_line,6), "hist": round(m - sig_line,6)}

    @staticmethod
    def support_resistance(closes: list, window: int = 20) -> dict:
        """Niveaux S/R simples."""
        if not closes: return {"support": 0.0, "resistance": 0.0, "mid": 0.0}
        w = closes[-window:] if len(closes) >= window else closes
        s, r = min(w), max(w)
        return {"support": round(s,6), "resistance": round(r,6), "mid": round((s+r)/2,6)}

    @staticmethod
    def trend_strength(closes: list, period: int = 20) -> dict:
        """Force de tendance linéaire. Retourne {"slope_pct","direction"}."""
        n = min(len(closes), period)
        if n < 3: return {"slope_pct": 0.0, "direction": "FLAT"}
        data = closes[-n:]
        xm, ym = (n-1)/2, sum(data)/n
        num = sum((i-xm)*(data[i]-ym) for i in range(n))
        den = sum((i-xm)**2 for i in range(n))
        sp = (num/den/ym*100) if den and ym else 0.0
        d = "UP" if sp > 0.05 else "DOWN" if sp < -0.05 else "FLAT"
        return {"slope_pct": round(sp,4), "direction": d}


# ═══════════════════════════════════════════════════════════════════
# AUTO-CALIBRATION — injectées dans BaseAgent après définition de classe
# ═══════════════════════════════════════════════════════════════════

def _calibrate_confidence(self, confidence: float, context: dict) -> float:
    """
    Ajuste la confiance selon la performance historique de cet agent.
    Lit /tmp/agent_perf.json (persisté par orchestrator.py).
    Pas d'ajustement si < 5 trades — données insuffisantes.
    """
    import os as _os, json as _json
    try:
        perf_file = "/tmp/agent_perf.json"
        if not _os.path.exists(perf_file): return confidence
        with open(perf_file, "r") as _f: perfs = _json.load(_f)
        key = self.name.lower().replace(" ","_").replace("-","_")
        p = perfs.get(key, {})
        total, wins = p.get("total", 0), p.get("wins", 0)
        if total < 5: return confidence
        wr = wins / total
        mult = 1.30 if wr >= 0.65 else 1.15 if wr >= 0.55 else 1.00 if wr >= 0.45 else 0.85 if wr >= 0.35 else 0.70
        return min(0.95, confidence * mult)
    except Exception:
        return confidence


def _regime_multiplier(self, signal: str, context: dict) -> float:
    """Multiplicateur de confiance basé sur le régime de marché."""
    regime = (context.get("market_regime") or "NEUTRAL").upper()
    sig    = (signal or "HOLD").upper()
    is_buy  = "BUY" in sig or "LONG" in sig
    is_sell = "SELL" in sig or "SHORT" in sig
    if "BULL" in regime:
        return 1.25 if is_buy  else 0.65 if is_sell else 0.90
    if "BEAR" in regime:
        return 1.25 if is_sell else 0.65 if is_buy  else 0.90
    return 0.85  # TRANSITIONAL/NEUTRAL → prudence


# Injection des méthodes dans BaseAgent (monkey-patch modulaire)
BaseAgent._calibrate_confidence = _calibrate_confidence
BaseAgent._regime_multiplier     = _regime_multiplier
BaseAgent.tools                  = TechnicalTools()
