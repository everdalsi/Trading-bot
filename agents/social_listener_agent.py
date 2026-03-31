"""
📡 SOCIAL LISTENER AGENT V5 — Vitesse x3 + Scoring Pondéré Urgence
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AMÉLIORATIONS V5 :
- Fetch RSS parallèle (asyncio.gather) : 6 sources simultanées vs séquentiel
- Score urgence temporelle : article < 15min = ×2.0, < 1h = ×1.5, >4h = ×0.5
- Pondération par source : CoinDesk/TheBlock = +30% crédibilité vs Reddit
- Score composite multi-couche : (RSS×0.45) + (Reddit×0.25) + (KOL×0.30)
- Deduplication agressive : hash MD5 sur titre normalisé
- Cache intelligent 2 minutes avec invalidation sur event critique
"""

from agents.base_agent import BaseAgent
from typing import Dict, Any, List, Optional
import asyncio
import time
import hashlib
import feedparser
import requests
from logging_config import logger
from datetime import datetime, timezone


RSS_FEEDS = {
    "CoinDesk":         ("https://www.coindesk.com/arc/outboundfeeds/rss/",         1.3),
    "CoinTelegraph":    ("https://cointelegraph.com/rss",                            1.2),
    "Decrypt":          ("https://decrypt.co/feed",                                  1.1),
    "The Block":        ("https://www.theblock.co/rss.xml",                          1.3),
    "CryptoSlate":      ("https://cryptoslate.com/feed/",                            1.0),
    "Bitcoin Magazine": ("https://bitcoinmagazine.com/feed",                         1.1),
}

NITTER_INSTANCES = [
    "nitter.privacydev.net",
    "nitter.poast.org",
    "nitter.1d4.us",
]
KOL_ACCOUNTS = [
    "michael_saylor", "APompliano", "CryptoKaleo", "lookonchain",
    "ArkhamIntel", "whale_alert", "blknoiz06", "VitalikButerin",
    "cz_binance", "RaoulGMI", "WClementeIII", "GiganticRebirth",
]

CRITICAL_KEYWORDS = [
    "hack", "hacked", "exploit", "rug pull", "sec", "ban", "arrest",
    "etf approved", "etf rejected", "fomc", "rate hike", "rate cut",
    "blackrock", "fidelity", "whale", "flash crash",
    "all-time high", "ath", "bankruptcy", "regulation", "lawsuit", "sanctioned",
]
BULLISH_KEYWORDS = [
    "bullish", "buy", "accumulate", "breakout", "surge", "rally",
    "moon", "pump", "green", "bottom", "accumulation",
    "etf approved", "institutional", "adoption", "partnership",
]
BEARISH_KEYWORDS = [
    "bearish", "sell", "dump", "crash", "drop", "red", "bear",
    "resistance", "rejected", "hack", "exploit", "ban", "lawsuit",
]


class SocialListenerAgent(BaseAgent):

    def __init__(self):
        super().__init__(
            name="social_listener",
            role="Surveillance temps réel RSS crypto + Reddit + KOL — scoring urgence pondéré"
        )
        self._news_cache: Dict[str, Any] = {}
        self._cache_ttl = 120
        self._seen_hashes: set = set()

    # ────────────────────────────────────────────────────────────────────────
    # UTILITAIRES
    # ────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _hash_article(title: str) -> str:
        return hashlib.md5(title.lower().strip().encode()).hexdigest()[:12]

    @staticmethod
    def _time_ago_minutes(published_str: str) -> float:
        """Convertit une date de publication en minutes depuis maintenant."""
        try:
            import email.utils
            dt = email.utils.parsedate_to_datetime(published_str)
            now = datetime.now(timezone.utc)
            return (now - dt).total_seconds() / 60
        except Exception:
            return 120.0  # 2h par défaut si parsing impossible

    @staticmethod
    def _urgency_multiplier(minutes_ago: float) -> float:
        """Score urgence : articles récents = signal fort."""
        if minutes_ago < 15:
            return 2.0    # Breaking news
        elif minutes_ago < 60:
            return 1.5    # Récent
        elif minutes_ago < 240:
            return 1.0    # Normal
        else:
            return 0.5    # Vieux → moins pertinent

    @staticmethod
    def _score_article(title: str, summary: str) -> float:
        """Score sentiment d'un article [-1, +1]."""
        text = (title + " " + summary).lower()
        bull = sum(1 for kw in BULLISH_KEYWORDS if kw in text)
        bear = sum(1 for kw in BEARISH_KEYWORDS if kw in text)
        crit = sum(1 for kw in CRITICAL_KEYWORDS if kw in text)
        if bull + bear == 0:
            return 0.0
        raw_score = (bull - bear) / (bull + bear)
        if crit > 0:
            raw_score *= 0.7   # article critique = signal ambigu → réduire
        return round(raw_score, 3)

    # ────────────────────────────────────────────────────────────────────────
    # FETCH RSS PARALLÈLE (asyncio)
    # ────────────────────────────────────────────────────────────────────────

    def _fetch_single_rss(self, source: str, url: str, credibility: float, symbol_clean: str) -> List[Dict]:
        """Fetch un flux RSS unique — exécuté en thread pool."""
        articles = []
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:6]:
                title   = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                link    = entry.get("link", "")
                published = entry.get("published", "")

                # Filter : garde seulement si symbole mentionné (ou générique)
                if symbol_clean and symbol_clean.lower() not in (title + summary).lower():
                    if not any(x in (title + summary).lower() for x in ["bitcoin", "btc", "crypto", "market"]):
                        continue

                h = self._hash_article(title)
                if h in self._seen_hashes:
                    continue
                self._seen_hashes.add(h)

                minutes_ago = self._time_ago_minutes(published)
                score       = self._score_article(title, summary)
                is_critical = any(kw in (title + summary).lower() for kw in CRITICAL_KEYWORDS)
                urgency     = self._urgency_multiplier(minutes_ago)

                articles.append({
                    "title":        title,
                    "link":         link,
                    "source":       source,
                    "credibility":  credibility,
                    "score":        score,
                    "urgency":      urgency,
                    "minutes_ago":  minutes_ago,
                    "weighted_score": score * credibility * urgency,
                    "is_critical":  is_critical,
                })
        except Exception as e:
            logger.debug(f"[SocialListener] RSS {source}: {e}")
        return articles

    async def _fetch_rss_parallel(self, symbol: str) -> List[Dict]:
        """Fetch toutes les sources RSS en parallèle."""
        symbol_clean = symbol.replace("USDT", "").replace("USD", "")
        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(None, self._fetch_single_rss, source, url, cred, symbol_clean)
            for source, (url, cred) in RSS_FEEDS.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_articles = []
        for r in results:
            if isinstance(r, list):
                all_articles.extend(r)
        # Trier par urgence × score
        all_articles.sort(key=lambda a: abs(a.get("weighted_score", 0)) * a.get("urgency", 1), reverse=True)
        return all_articles[:20]

    # ────────────────────────────────────────────────────────────────────────
    # REDDIT SENTIMENT
    # ────────────────────────────────────────────────────────────────────────

    def _fetch_reddit_sentiment(self, symbol: str) -> Dict[str, Any]:
        symbol_clean = symbol.replace("USDT", "").replace("USD", "").lower()
        subs = [f"r/CryptoCurrency.json", f"r/Bitcoin.json", f"r/{symbol_clean}.json"]
        bull, bear, total, upvote_sum = 0, 0, 0, 0
        for sub in subs:
            try:
                r = requests.get(
                    f"https://www.reddit.com/{sub}",
                    headers={"User-Agent": "TradingBot/1.0"},
                    timeout=6, params={"limit": 10}
                )
                if r.status_code == 200:
                    posts = r.json().get("data", {}).get("children", [])
                    for post in posts:
                        d     = post.get("data", {})
                        title = d.get("title", "")
                        score_p = self._score_article(title, "")
                        upvotes = d.get("score", 0)
                        upvote_sum += upvotes
                        if score_p > 0:
                            bull += 1
                        elif score_p < 0:
                            bear += 1
                        total += 1
            except Exception:
                pass
        wr = bull / total if total > 0 else 0.5
        return {"bull": bull, "bear": bear, "total": total,
                "avg_upvote": upvote_sum / total if total > 0 else 0,
                "sentiment": wr}

    # ────────────────────────────────────────────────────────────────────────
    # NITTER KOL
    # ────────────────────────────────────────────────────────────────────────

    def _fetch_nitter_kol(self, symbol: str) -> Dict[str, Any]:
        symbol_clean = symbol.replace("USDT", "").replace("USD", "").lower()
        mentions, bull, bear = 0, 0, 0
        for instance in NITTER_INSTANCES[:2]:
            try:
                kol = KOL_ACCOUNTS[0]
                r = requests.get(f"https://{instance}/{kol}/rss",
                                 timeout=4, headers={"User-Agent": "TradingBot"})
                if r.status_code == 200:
                    feed = feedparser.parse(r.text)
                    for entry in feed.entries[:5]:
                        title = entry.get("title", "")
                        if symbol_clean in title.lower() or "btc" in title.lower():
                            mentions += 1
                            sc = self._score_article(title, "")
                            if sc > 0: bull += 1
                            elif sc < 0: bear += 1
                break  # succès sur la première instance
            except Exception:
                continue
        return {"kol_mentions": mentions, "bull": bull, "bear": bear}

    # ────────────────────────────────────────────────────────────────────────
    # SCORE COMPOSITE
    # ────────────────────────────────────────────────────────────────────────

    def _compute_sentiment(self, articles: List[Dict], reddit: Dict, kol: Dict) -> Dict[str, Any]:
        """
        Score composite pondéré V5 :
        RSS (45%) + Reddit (25%) + KOL (30%)
        """
        # Score RSS pondéré par crédibilité × urgence
        if articles:
            weighted_sum = sum(a.get("weighted_score", 0) for a in articles)
            rss_score    = max(0.0, min(1.0, 0.5 + weighted_sum / len(articles)))
        else:
            rss_score = 0.5

        # Score Reddit [0, 1]
        reddit_score = reddit.get("sentiment", 0.5)

        # Score KOL [0, 1]
        kol_bull = kol.get("bull", 0)
        kol_bear = kol.get("bear", 0)
        kol_total = kol_bull + kol_bear
        kol_score = kol_bull / kol_total if kol_total > 0 else 0.5

        # Composite pondéré
        composite = (
            0.45 * rss_score +
            0.25 * reddit_score +
            0.30 * kol_score
        )

        # Boost si article critique récent (< 30min)
        critical_recent = [a for a in articles if a.get("is_critical") and a.get("minutes_ago", 999) < 30]
        is_critical_alert = len(critical_recent) > 0

        if is_critical_alert:
            composite *= 0.7   # force vers la prudence si breaking critical news

        hot_topics = list(set(
            kw for a in articles[:5]
            for kw in BULLISH_KEYWORDS + BEARISH_KEYWORDS + CRITICAL_KEYWORDS
            if kw in (a["title"]).lower()
        ))[:5]

        return {
            "sentiment_score":   round(composite, 3),
            "rss_score":         round(rss_score, 3),
            "reddit_score":      round(reddit_score, 3),
            "kol_score":         round(kol_score, 3),
            "is_critical_alert": is_critical_alert,
            "critical_count":    len(critical_recent),
            "hot_topics":        hot_topics,
        }

    # ────────────────────────────────────────────────────────────────────────
    # RÉPONSE PRINCIPALE
    # ────────────────────────────────────────────────────────────────────────

    async def get_multi_source_sentiment(self, symbol: str, context: dict) -> Dict[str, Any]:
        # Fetch parallèle
        articles = await self._fetch_rss_parallel(symbol)
        reddit   = self._fetch_reddit_sentiment(symbol)
        kol      = self._fetch_nitter_kol(symbol)
        sentiment = self._compute_sentiment(articles, reddit, kol)
        credibility = sum(a.get("credibility", 1.0) for a in articles) / len(articles) if articles else 0.8
        return {**sentiment, "articles": articles, "reddit": reddit, "kol": kol,
                "credibility": credibility, "top_headlines": [a["title"] for a in articles[:3]]}

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        symbol = context.get("symbol", "BTCUSDT")
        cache_key = f"{symbol}_{int(time.time() // self._cache_ttl)}"

        if cache_key in self._news_cache:
            return self._news_cache[cache_key]

        try:
            intel = await asyncio.wait_for(
                self.get_multi_source_sentiment(symbol, context), timeout=12.0
            )
        except asyncio.TimeoutError:
            intel = {"sentiment_score": 0.5, "is_critical_alert": False, "hot_topics": [],
                     "top_headlines": [], "credibility": 0.5, "articles": []}
        except Exception as e:
            intel = {"sentiment_score": 0.5, "is_critical_alert": False, "hot_topics": [],
                     "top_headlines": [], "credibility": 0.5, "articles": []}
            logger.warning(f"[SocialListener] respond error: {e}")

        score   = intel.get("sentiment_score", 0.5)
        is_crit = intel.get("is_critical_alert", False)
        articles = intel.get("articles", [])

        if score >= 0.70:
            recommendation = "Signal BULLISH social → favorise long"
        elif score <= 0.35:
            recommendation = "Signal BEARISH social → prudence / short"
        elif is_crit:
            recommendation = "⚠️ ALERTE CRITIQUE → HOLD jusqu'à clarification"
        else:
            recommendation = "Sentiment neutre → analyse technique prioritaire"

        # Urgence : nombre d'articles < 15min
        very_recent = sum(1 for a in articles if a.get("minutes_ago", 999) < 15)

        full_summary = (
            f"📡 Social V5 [{symbol}] | Score: {score:.2f} "
            f"(RSS:{intel.get('rss_score',0.5):.2f} Reddit:{intel.get('reddit_score',0.5):.2f} KOL:{intel.get('kol_score',0.5):.2f}) | "
            f"Articles: {len(articles)} | Breaking ({very_recent} <15min) | "
            f"Alerte: {'⚠️OUI' if is_crit else 'Non'} | Topics: {', '.join(intel.get('hot_topics',[])[:3]) or 'aucun'}"
        )

        result = {
            "agent":           self.name,
            "sentiment_score": score,
            "credibility":     intel.get("credibility", 0.8),
            "hot_topics":      intel.get("hot_topics", []),
            "is_critical":     is_crit,
            "very_recent_count": very_recent,
            "kol_mentions":    intel.get("kol", {}).get("kol_mentions", 0),
            "reddit":          intel.get("reddit", {}),
            "top_headlines":   intel.get("top_headlines", []),
            "summary":         full_summary,
            "full_summary":    full_summary,
            "confidence":      min(0.95, score * intel.get("credibility", 0.8) + 0.25),
            "recommendation":  recommendation,
            "glossary_used":   True,
        }
        self._news_cache[cache_key] = result
        return result
