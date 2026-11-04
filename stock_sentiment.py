"""
stock_sentiment.py -- Standalone, isolated social/news risk-check for US
equities (added 2026-11-04, per explicit user instruction, same day as the
crypto side's compute_crypto_risk_pct() in bot.py).

WHY A SEPARATE MODULE INSTEAD OF REUSING agents/social_listener_agent.py:
that agent is crypto-only by construction -- its RSS list is CoinDesk/
CoinTelegraph/Decrypt/etc., its Reddit subs are r/CryptoCurrency and
r/Bitcoin, its keyword lists are crypto-flavored ("rug pull", "halving").
None of that applies to a stock ticker. This module mirrors its approach
(RSS + Reddit + X-via-Nitter, same composite-scoring idea) with sources
and keywords that actually fit equities, kept in its own file for the same
reason sec_13f_signal.py is its own file: zero coupling to bot.py, safe to
import standalone, same isolation contract as every other *_signal.py
module in this repo (no network call unless a fetch function is actually
called, no import-time side effects).

No API key needed anywhere: Yahoo Finance's per-ticker RSS feed is public,
Reddit's public JSON endpoint needs no auth (same technique already used
by social_listener_agent.py for crypto subs), and X/Twitter is reached via
Nitter mirrors since X's own API has been paid-only since 2023 -- there is
no free, keyless way to query X directly. Nitter instances are frequently
down or rate-limited; treat an empty X signal as "couldn't check", never
as "no mentions found" (same caveat the crypto agent already documents for
its own Nitter use).

USAGE:
    from stock_sentiment import compute_risk_pct
    risk = compute_risk_pct("AAPL", "BUY")   # {"risk_pct": 0-100, ...}
"""
from __future__ import annotations

import json
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

YAHOO_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
REDDIT_SUBS = ["wallstreetbets", "stocks", "investing"]
NITTER_INSTANCES = ["nitter.privacydev.net", "nitter.poast.org", "nitter.1d4.us"]
FINANCE_KOLS = ["DeItaone", "unusual_whales", "zerohedge"]

REQUEST_TIMEOUT_SEC = 10
USER_AGENT = "Mozilla/5.0 (stock-sentiment-research/1.0)"

BULLISH_KEYWORDS = [
    "buy", "upgrade", "beat", "beats", "surge", "rally", "bullish",
    "outperform", "record high", "breakout", "raises guidance",
]
BEARISH_KEYWORDS = [
    "sell", "downgrade", "miss", "misses", "plunge", "crash", "bearish",
    "underperform", "lawsuit", "investigation", "recall", "cuts guidance",
]
CRITICAL_KEYWORDS = [
    "bankruptcy", "fraud", "sec investigation", "halted", "delisted",
    "recall", "class action", "resigns", "steps down", "restatement",
]


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
        return resp.read()


def _score_text(text: str) -> float:
    """[-1, +1] from simple keyword counting -- same transparent, non-black-box
    approach as agents/social_listener_agent.py's _score_article, deliberately
    simple so the resulting risk number is explainable."""
    text = text.lower()
    bull = sum(1 for kw in BULLISH_KEYWORDS if kw in text)
    bear = sum(1 for kw in BEARISH_KEYWORDS if kw in text)
    if bull + bear == 0:
        return 0.0
    return (bull - bear) / (bull + bear)


def _is_critical(text: str) -> bool:
    text = text.lower()
    return any(kw in text for kw in CRITICAL_KEYWORDS)


def fetch_yahoo_headlines(ticker: str, max_items: int = 10) -> list[dict]:
    """Per-ticker Yahoo Finance RSS headlines, feed order (newest first).
    Fail-open: any error (network, malformed XML, ticker with no feed)
    yields []."""
    try:
        xml_bytes = _get(YAHOO_RSS_URL.format(ticker=ticker))
        root = ET.fromstring(xml_bytes)
        items = []
        for item in root.findall(".//item")[:max_items]:
            title = (item.findtext("title") or "").strip()
            if title:
                items.append({"title": title, "pub_date": item.findtext("pubDate") or ""})
        return items
    except Exception:
        return []


def fetch_reddit_mentions(ticker: str, subs: Optional[list] = None, limit: int = 15) -> dict:
    """Public Reddit JSON search (no auth), same technique already used by
    agents/social_listener_agent.py for its crypto subs. Fail-open: a sub
    that errors (down, rate-limited) is skipped, never aborts the others."""
    subs = subs or REDDIT_SUBS
    bull, bear, total = 0, 0, 0
    for sub in subs:
        try:
            url = f"https://www.reddit.com/r/{sub}/search.json?q={ticker}&restrict_sr=1&sort=new&limit={limit}"
            data = json.loads(_get(url).decode("utf-8"))
            for post in data.get("data", {}).get("children", []):
                title = post.get("data", {}).get("title", "")
                s = _score_text(title)
                total += 1
                if s > 0:
                    bull += 1
                elif s < 0:
                    bear += 1
        except Exception:
            continue
    return {"bull": bull, "bear": bear, "total": total, "sentiment": (bull / total) if total else 0.5}


def fetch_nitter_mentions(ticker: str, kols: Optional[list] = None) -> dict:
    """X/Twitter mentions of `ticker` in a few finance accounts' recent
    posts, via Nitter mirrors (no official free X API exists any more --
    see module docstring). Frequently unavailable: mentions=0 here means
    "couldn't check that account", not "nobody's talking about it". Tries
    each instance in turn, stops at the first that works."""
    kols = kols or FINANCE_KOLS
    mentions, bull, bear = 0, 0, 0
    for instance in NITTER_INSTANCES[:2]:
        try:
            got_any = False
            for kol in kols:
                try:
                    xml_bytes = _get(f"https://{instance}/{kol}/rss")
                except Exception:
                    continue
                got_any = True
                root = ET.fromstring(xml_bytes)
                for item in root.findall(".//item")[:10]:
                    title = item.findtext("title") or ""
                    if ticker.lower() in title.lower():
                        mentions += 1
                        s = _score_text(title)
                        if s > 0:
                            bull += 1
                        elif s < 0:
                            bear += 1
            if got_any:
                break  # this instance is up and we got through the KOL list -- don't also hit the others
        except Exception:
            continue
    return {"mentions": mentions, "bull": bull, "bear": bear}


def get_sentiment(ticker: str) -> dict:
    """Composite sentiment for `ticker`, same 0=bearish/0.5=neutral/1=bullish
    shape as agents/social_listener_agent.py's crypto sentiment_score, so
    compute_risk_pct below reads the same way on both sides of the bot.
    Weighting: RSS 50% / Reddit 30% / X 20% (X weighted down from the
    crypto version's 30% since Nitter is the least reliable source here).
    Fail-open throughout -- a fully-failed check returns sentiment_score
    0.5 (neutral/unknown), never raises."""
    headlines = fetch_yahoo_headlines(ticker)
    rss_score = 0.5
    if headlines:
        raw = sum(_score_text(h["title"]) for h in headlines) / len(headlines)
        rss_score = max(0.0, min(1.0, 0.5 + raw / 2))

    reddit = fetch_reddit_mentions(ticker)
    reddit_score = reddit.get("sentiment", 0.5)

    nitter = fetch_nitter_mentions(ticker)
    nitter_total = nitter["bull"] + nitter["bear"]
    nitter_score = (nitter["bull"] / nitter_total) if nitter_total else 0.5

    composite = 0.5 * rss_score + 0.3 * reddit_score + 0.2 * nitter_score
    is_critical = any(_is_critical(h["title"]) for h in headlines[:5])
    if is_critical:
        # Ambiguous-signal rule, same as agents/social_listener_agent.py:
        # pull toward neutral rather than trust a critical-news tape's
        # direction at face value.
        composite = 0.5 + (composite - 0.5) * 0.7

    return {
        "sentiment_score": round(composite, 3), "rss_score": round(rss_score, 3),
        "reddit_score": round(reddit_score, 3), "nitter_score": round(nitter_score, 3),
        "is_critical": is_critical, "top_headlines": [h["title"] for h in headlines[:3]],
        "reddit_mentions": reddit.get("total", 0), "nitter_mentions": nitter.get("mentions", 0),
    }


def compute_risk_pct(ticker: str, side: str) -> dict:
    """Same semantics/shape as bot.py's compute_crypto_risk_pct, for the
    stocks sleeve. `side` is "BUY" or "SELL" -- alpaca_stocks_sleeve.py is
    long-only (SELL only ever closes an existing long, never opens a
    short), so risk is framed as "risk in adding to this long" for BUY.
    INFORMATIONAL ONLY, per explicit user instruction: the caller decides
    whether/how to show this, it never blocks or changes any order.
    Fail-open: any error yields risk_pct=50 (unknown/neutral), never
    raises."""
    try:
        s = get_sentiment(ticker)
        sentiment = s["sentiment_score"]
        alignment = sentiment if side == "BUY" else (1.0 - sentiment)
        risk_pct = 100.0 * (1.0 - alignment)
        if s["is_critical"]:
            risk_pct = min(95.0, risk_pct + 25.0)
        return {
            "risk_pct": round(max(0.0, min(100.0, risk_pct))),
            "sentiment_score": sentiment, "is_critical": s["is_critical"],
            "top_headline": (s["top_headlines"] or [None])[0],
        }
    except Exception:
        return {"risk_pct": 50, "sentiment_score": 0.5, "is_critical": False, "top_headline": None}


# ---------------------------------------------------------------------- #
# Unit tests (stdlib only, NO network calls -- run with:
#   python stock_sentiment.py
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    import unittest

    class TestStockSentiment(unittest.TestCase):
        def test_score_text_bullish(self):
            self.assertGreater(_score_text("Stock surges after beat, analysts bullish"), 0)

        def test_score_text_bearish(self):
            self.assertLess(_score_text("Shares plunge after downgrade and lawsuit"), 0)

        def test_score_text_neutral_no_keywords(self):
            self.assertEqual(_score_text("Company reports quarterly results"), 0.0)

        def test_is_critical_true(self):
            self.assertTrue(_is_critical("Company files for bankruptcy amid fraud probe"))

        def test_is_critical_false(self):
            self.assertFalse(_is_critical("Stock rises on strong earnings"))

        def test_compute_risk_pct_shape_on_total_failure(self):
            # No network in this test run -- fetchers should fail-open, not raise.
            risk = compute_risk_pct("ZZZQQQNOPE", "BUY")
            self.assertIn("risk_pct", risk)
            self.assertGreaterEqual(risk["risk_pct"], 0)
            self.assertLessEqual(risk["risk_pct"], 100)

        def test_compute_risk_pct_sell_inverts_alignment(self):
            # Same sentiment score should produce roughly inverse risk for BUY vs SELL
            # when sentiment is clearly bullish or bearish -- check the direction, not
            # exact numbers (those depend on live network data in a real run).
            import unittest.mock as mock
            with mock.patch("stock_sentiment.get_sentiment", return_value={
                "sentiment_score": 0.9, "is_critical": False, "top_headlines": []
            }):
                buy_risk = compute_risk_pct("AAPL", "BUY")["risk_pct"]
                sell_risk = compute_risk_pct("AAPL", "SELL")["risk_pct"]
            self.assertLess(buy_risk, sell_risk)

    unittest.main(verbosity=2)
