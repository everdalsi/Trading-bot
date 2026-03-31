"""
🎙️ SOCIAL LISTENER AGENT V4 — Surveillance réelle multi-sources + vérification fake news
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Rôle :
- Écoute RSS/news crypto en temps réel (CoinDesk, CoinTelegraph, Decrypt, The Block)
- Scraping Reddit r/CryptoCurrency, r/Bitcoin, r/solana via PRAW ou JSON
- Monitoring Nitter (X/Twitter) des KOLs et baleines
- Scoring sentiment précis (0→1) avec vérification croisée anti-fake
- Alerte immédiate si event majeur détecté (hack, ETF, ban, FOMC, etc.)
"""

from agents.base_agent import BaseAgent
from typing import Dict, Any, List
import asyncio
import requests
import time
import json
import hashlib
import feedparser
from logging_config import logger


# ────────────────────────────────────────────────────────────────────────────
# SOURCES RSS CRYPTO RÉELLES
# ────────────────────────────────────────────────────────────────────────────
RSS_FEEDS = {
    "CoinDesk":      "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "CoinTelegraph": "https://cointelegraph.com/rss",
    "Decrypt":       "https://decrypt.co/feed",
    "The Block":     "https://www.theblock.co/rss.xml",
    "CryptoSlate":   "https://cryptoslate.com/feed/",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/feed",
}

# Comptes KOL à surveiller (via Nitter instances)
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

# Mots-clés d'alerte critique (impact marché fort)
CRITICAL_KEYWORDS = [
    "hack", "hacked", "exploit", "rug pull", "sec", "ban", "arrest",
    "etf approved", "etf rejected", "fomc", "rate hike", "rate cut",
    "blackrock", "fidelity", "whale", "liquidation", "flash crash",
    "all-time high", "ath", "all-time low", "atl", "bankruptcy",
    "regulation", "lawsuit", "sanctioned", "sanctioned",
]
BULLISH_KEYWORDS = [
    "bullish", "buy", "accumulate", "breakout", "surge", "rally",
    "moon", "pump", "green", "support", "bottom", "accumulation",
    "etf approved", "institutional", "adoption", "partnership",
]
BEARISH_KEYWORDS = [
    "bearish", "sell", "dump", "crash", "drop", "red", "bear",
    "resistance", "rejected", "hack", "exploit", "ban", "lawsuit",
]


class SocialListenerAgent(BaseAgent):
    """ÉCOUTE LIVE RÉSEAUX SOCIAUX, NEWS & DISCUSSIONS TRADERS PROS"""

    def __init__(self):
        super().__init__(
            name="social_listener",
            role=(
                "Surveillance temps réel : RSS news crypto, Reddit, Nitter (X/Twitter KOLs), "
                "scoring sentiment précis + vérification croisée anti-fake news"
            )
        )
        self._news_cache: Dict[str, Any] = {}
        self._cache_ttl = 120  # 2 min cache
        self._seen_hashes: set = set()  # déduplique les articles

    # ────────────────────────────────────────────────────────────────────────
    # COLLECTE NEWS RÉELLES
    # ────────────────────────────────────────────────────────────────────────

    def _hash_article(self, title: str) -> str:
        return hashlib.md5(title.lower().strip().encode()).hexdigest()[:12]

    def _fetch_rss_news(self, symbol: str = "BTC", limit: int = 20) -> List[Dict]:
        """Lit les flux RSS réels des médias crypto."""
        articles = []
        symbol_clean = symbol.replace("USDT", "").replace("USD", "")

        for source, url in RSS_FEEDS.items():
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:5]:
                    title = entry.get("title", "")
                    summary = entry.get("summary", "")
                    link = entry.get("link", "")
                    h = self._hash_article(title)
                    if h in self._seen_hashes:
                        continue
                    self._seen_hashes.add(h)
                    # Filtre par symbole si mentionné
                    combined = (title + " " + summary).lower()
                    if symbol_clean.lower() in combined or "bitcoin" in combined or "crypto" in combined:
                        articles.append({
                            "source":  source,
                            "title":   title,
                            "summary": summary[:200],
                            "url":     link,
                        })
                        if len(articles) >= limit:
                            break
            except Exception as e:
                logger.debug(f"[SOCIAL] RSS {source} error: {e}")
            if len(articles) >= limit:
                break

        return articles

    def _fetch_reddit_sentiment(self, symbol: str = "BTC") -> Dict[str, Any]:
        """Scraping Reddit via API JSON publique (sans authentification)."""
        symbol_clean = symbol.replace("USDT", "").replace("USD", "")
        subreddits = ["CryptoCurrency", "Bitcoin", "CryptoMarkets", "SatoshiStreetBets"]
        posts = []
        for sub in subreddits[:2]:
            try:
                r = requests.get(
                    f"https://www.reddit.com/r/{sub}/hot.json?limit=15",
                    headers={"User-Agent": "trading-bot-social-listener/1.0"},
                    timeout=8
                )
                if r.status_code == 200:
                    for post in r.json().get("data", {}).get("children", []):
                        d = post.get("data", {})
                        title = d.get("title", "")
                        if symbol_clean.lower() in title.lower() or "bitcoin" in title.lower() or "crypto" in title.lower():
                            posts.append({
                                "title": title,
                                "score": d.get("score", 0),
                                "comments": d.get("num_comments", 0),
                                "upvote_ratio": d.get("upvote_ratio", 0.5),
                            })
            except Exception as e:
                logger.debug(f"[SOCIAL] Reddit {sub} error: {e}")

        if not posts:
            return {"posts": 0, "avg_upvote": 0.5, "avg_score": 0}

        avg_upvote = sum(p["upvote_ratio"] for p in posts) / len(posts)
        avg_score  = sum(p["score"] for p in posts) / len(posts)
        return {
            "posts":       len(posts),
            "avg_upvote":  round(avg_upvote, 3),
            "avg_score":   round(avg_score, 1),
            "top_posts":   [p["title"] for p in sorted(posts, key=lambda x: -x["score"])[:3]],
        }

    def _fetch_nitter_kol(self, symbol: str = "BTC") -> Dict[str, Any]:
        """Scraping Nitter pour surveiller les KOLs (sans API Twitter)."""
        symbol_clean = symbol.replace("USDT", "").replace("USD", "")
        mentions = 0
        bullish_mentions = 0
        bearish_mentions = 0
        hot_topics: List[str] = []

        for instance in NITTER_INSTANCES[:2]:
            for account in KOL_ACCOUNTS[:4]:
                try:
                    r = requests.get(
                        f"https://{instance}/{account}/rss",
                        timeout=6
                    )
                    if r.status_code == 200:
                        feed = feedparser.parse(r.text)
                        for entry in feed.entries[:5]:
                            text = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
                            if symbol_clean.lower() in text or "btc" in text or "crypto" in text:
                                mentions += 1
                                bull = sum(1 for kw in BULLISH_KEYWORDS if kw in text)
                                bear = sum(1 for kw in BEARISH_KEYWORDS if kw in text)
                                bullish_mentions += bull
                                bearish_mentions += bear
                                for kw in CRITICAL_KEYWORDS:
                                    if kw in text and kw not in hot_topics:
                                        hot_topics.append(kw)
                except Exception:
                    pass

        return {
            "kol_mentions":     mentions,
            "bullish_mentions": bullish_mentions,
            "bearish_mentions": bearish_mentions,
            "hot_topics":       hot_topics[:5],
        }

    # ────────────────────────────────────────────────────────────────────────
    # SCORING SENTIMENT
    # ────────────────────────────────────────────────────────────────────────

    def _compute_sentiment(
        self,
        articles: List[Dict],
        reddit: Dict,
        kol: Dict,
    ) -> Dict[str, Any]:
        """Calcule un score de sentiment global et détecte les événements critiques."""
        text_blob = " ".join(
            a["title"] + " " + a.get("summary", "") for a in articles
        ).lower()

        bull_count = sum(text_blob.count(kw) for kw in BULLISH_KEYWORDS)
        bear_count = sum(text_blob.count(kw) for kw in BEARISH_KEYWORDS)
        crit_count = sum(text_blob.count(kw) for kw in CRITICAL_KEYWORDS)

        # Ajout Reddit
        bull_count += kol["bullish_mentions"]
        bear_count += kol["bearish_mentions"]

        total_signals = bull_count + bear_count + 1e-9
        raw_score = bull_count / total_signals

        # Facteur Reddit upvote (0.5 = neutre, 1.0 = très bullish)
        reddit_factor = reddit.get("avg_upvote", 0.5)
        sentiment_score = 0.65 * raw_score + 0.35 * reddit_factor

        # Pénalité si événement critique détecté (incertitude)
        if crit_count > 0:
            sentiment_score *= 0.80

        hot_topics = kol["hot_topics"] + [
            kw for kw in CRITICAL_KEYWORDS if kw in text_blob
        ]
        hot_topics = list(set(hot_topics))[:5]

        # Détection alerte critique
        is_critical = crit_count >= 2 or len(hot_topics) >= 2

        return {
            "sentiment_score":    round(min(1.0, max(0.0, sentiment_score)), 3),
            "bull_signals":       bull_count,
            "bear_signals":       bear_count,
            "critical_events":    crit_count,
            "hot_topics":         hot_topics,
            "is_critical_alert":  is_critical,
            "reddit_upvote_avg":  reddit_factor,
            "kol_mentions":       kol["kol_mentions"],
            "sources_checked":    len(articles),
        }

    # ────────────────────────────────────────────────────────────────────────
    # VÉRIFICATION ANTI-FAKE
    # ────────────────────────────────────────────────────────────────────────

    def _verify_news_credibility(self, articles: List[Dict]) -> float:
        """
        Vérifie la crédibilité des news en croisant les sources.
        Si plusieurs sources indépendantes parlent du même fait → plus crédible.
        """
        if len(articles) < 2:
            return 0.5  # pas assez de sources pour vérifier

        titles_lower = [a["title"].lower() for a in articles]
        cross_confirmed = 0
        for i, t1 in enumerate(titles_lower):
            for t2 in titles_lower[i+1:]:
                # Cherche des mots-clés communs (> 2 mots)
                words1 = set(t1.split())
                words2 = set(t2.split())
                common = words1 & words2 - {"the","a","an","in","of","to","and","or","is","was","for","on"}
                if len(common) >= 3:
                    cross_confirmed += 1

        credibility = min(1.0, 0.5 + cross_confirmed * 0.15)
        return round(credibility, 2)

    # ────────────────────────────────────────────────────────────────────────
    # DOMAINE & RÉPONSE
    # ────────────────────────────────────────────────────────────────────────

    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        keywords = [
            "social", "sentiment", "news", "rss", "reddit", "twitter", "nitter",
            "kol", "whale", "ecoute", "écoute", "actualité", "actualite",
            "fake", "information", "media", "source", "buzz",
            # participation débat collectif
            "synthèse", "débat", "cerveau collectif", "final decision", "raffine",
            "trade ou no trade", "micro", "analyse collective",
        ]
        return any(kw in q for kw in keywords)

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {
                "agent":          self.name,
                "summary":        f"⚠️ {self.name} hors spécialité → ignoré",
                "confidence":     0.0,
                "recommendation": "HOLD - Ignoré par spécialisation stricte",
                "warning":        "Hors domaine social_listener",
            }

        shared_glossary = context.get("shared_glossary", {})
        def explain(k):
            return self.explain_term(k) or shared_glossary.get(k, k)

        symbol = context.get("symbol", "BTCUSDT")
        logger.info(f"[SOCIAL LISTENER] 🔴 Écoute live sur {symbol}...")

        # Cache check
        cache_key = f"{symbol}_{int(time.time() / self._cache_ttl)}"
        if cache_key in self._news_cache:
            return self._news_cache[cache_key]

        # Collecte parallèle (simulée par appels séquentiels rapides)
        articles = self._fetch_rss_news(symbol, limit=15)
        reddit   = self._fetch_reddit_sentiment(symbol)
        kol      = self._fetch_nitter_kol(symbol)

        sentiment = self._compute_sentiment(articles, reddit, kol)
        credibility = self._verify_news_credibility(articles)

        # Résumé des titres les plus importants
        top_headlines = [a["title"] for a in articles[:3]]

        # Recommandation basée sur le sentiment
        score = sentiment["sentiment_score"]
        if score >= 0.70:
            reco = "Signal BULLISH social détecté → favorise un trade long"
        elif score <= 0.35:
            reco = "Signal BEARISH social détecté → prudence / short envisageable"
        elif sentiment["is_critical_alert"]:
            reco = "⚠️ ALERTE CRITIQUE détectée → HOLD jusqu'à clarification"
        else:
            reco = "Sentiment neutre → se baser sur l'analyse technique"

        full_summary = (
            f"📡 Social Listener — {symbol} | Sources analysées: {len(articles)} articles, "
            f"Reddit upvote avg: {reddit.get('avg_upvote', 0.5):.0%}, "
            f"KOL mentions: {kol['kol_mentions']} | "
            f"Sentiment: {score:.2f} | Crédibilité: {credibility:.0%} | "
            f"Topics chauds: {', '.join(sentiment['hot_topics']) or 'aucun'} | "
            f"Alerte critique: {'OUI ⚠️' if sentiment['is_critical_alert'] else 'Non'}"
        )

        result = {
            "agent":             self.name,
            "sentiment_score":   score,
            "credibility":       credibility,
            "hot_topics":        sentiment["hot_topics"],
            "is_critical":       sentiment["is_critical_alert"],
            "kol_mentions":      kol["kol_mentions"],
            "reddit":            reddit,
            "top_headlines":     top_headlines,
            "summary":           full_summary,
            "full_summary":      full_summary,
            "confidence":        min(0.95, score * credibility + 0.2),
            "recommendation":    reco,
            "glossary_used":     True,
        }
        self._news_cache[cache_key] = result
        return result
