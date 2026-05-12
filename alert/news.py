from datetime import datetime, timezone, timedelta

import feedparser

FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("CryptoPanic", "https://cryptopanic.com/news/rss/?currencies=ETH"),
]

ETH_KEYWORDS = {"ethereum", "eth", "$eth"}


def fetch_recent_eth_news(hours=24):
    """
    Fetch ETH-related articles published in the last N hours from RSS feeds.
    Returns list of {source, title, summary, published}.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    articles = []

    for source_name, feed_url in FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                published = _parse_published(entry)
                if published and published < cutoff:
                    continue
                text = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
                if any(kw in text for kw in ETH_KEYWORDS):
                    articles.append({
                        "source": source_name,
                        "title": entry.get("title", "").strip(),
                        "summary": entry.get("summary", "")[:300].strip(),
                        "published": published,
                    })
        except Exception as exc:
            print(f"[news] Failed to fetch {source_name}: {exc}")

    return articles[:20]


def _parse_published(entry):
    try:
        ts = entry.get("published_parsed") or entry.get("updated_parsed")
        if ts:
            return datetime(*ts[:6], tzinfo=timezone.utc)
    except Exception:
        pass
    return None
