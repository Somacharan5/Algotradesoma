"""
News layer — recent headlines for a stock and for the broad market.

Sources Google News RSS (free, no API key). Headlines are scored with a
finance-tuned lexicon: net sentiment in [-1, +1], plus a hard "red flag"
veto for events that should always block a fresh buy (fraud probe, raid,
default …) regardless of how good the chart looks.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from loguru import logger

_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
_LOOKBACK_DAYS = 5
_MAX_HEADLINES = 12

POSITIVE = {
    "beats", "beat", "surge", "surges", "soars", "record", "upgrade", "upgrades",
    "upgraded", "buyback", "bonus", "dividend", "wins", "win", "order", "orders",
    "profit jumps", "profit rises", "profit up", "growth", "expansion", "expands",
    "acquisition", "acquires", "approval", "approved", "launch", "launches",
    "partnership", "rally", "rallies", "gains", "outperform", "strong", "robust",
    "highest", "milestone", "breakthrough", "raises guidance", "upbeat",
}
NEGATIVE = {
    "falls", "fall", "drops", "drop", "plunge", "plunges", "slump", "slumps",
    "loss", "losses", "downgrade", "downgrades", "downgraded", "misses", "miss",
    "weak", "cuts guidance", "lawsuit", "fine", "fined", "strike", "recall",
    "resigns", "resignation", "exit", "layoff", "layoffs", "debt woes",
    "underperform", "sell-off", "selloff", "warning", "warns", "concern",
    "profit falls", "profit drops", "declines", "decline", "crisis",
}
# Any of these in a headline → hard veto on new buys, whatever else says
RED_FLAGS = {
    "fraud", "scam", "probe", "raid", "raids", "investigation", "default",
    "insolvency", "bankruptcy", "arrested", "arrest", "sebi action", "sebi bars",
    "ban", "banned", "show cause", "money laundering", "ed summons", "cbi",
    "manipulation", "auditor resigns", "delisting", "bans", "bars",
}


@dataclass
class NewsReport:
    query: str
    sentiment: float = 0.0          # -1 … +1 (0 = neutral / no news)
    headline_count: int = 0
    red_flag: bool = False
    red_flag_headline: str = ""
    top_positive: str = ""
    top_negative: str = ""
    headlines: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def fetch_headlines(query: str, lookback_days: int = _LOOKBACK_DAYS) -> list[str]:
    """Recent headlines for a search query, newest first."""
    import feedparser  # lazy import

    url = _RSS_URL.format(query=urllib.parse.quote(query))
    feed = feedparser.parse(url)
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    titles: list[str] = []
    for entry in feed.entries[: _MAX_HEADLINES * 3]:
        try:
            published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            if published < cutoff:
                continue
        except (AttributeError, TypeError):
            pass  # keep undated entries
        title = re.sub(r"\s+-\s+[^-]+$", "", entry.title)  # strip " - Source"
        titles.append(title)
        if len(titles) >= _MAX_HEADLINES:
            break
    return titles


def _compile(terms: set[str]) -> re.Pattern:
    """Whole-word/phrase matcher — 'ban' must not match inside 'Bank'."""
    alts = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"\b(?:{alts})\b")


_POS_RE = _compile(POSITIVE)
_NEG_RE = _compile(NEGATIVE)
_RED_RE = _compile(RED_FLAGS)


def _score_headline(title: str) -> tuple[int, bool]:
    """(sentiment -1/0/+1, is_red_flag) for one headline."""
    low = title.lower()
    if _RED_RE.search(low):
        return -1, True
    pos = len(_POS_RE.findall(low))
    neg = len(_NEG_RE.findall(low))
    return (1 if pos > neg else -1 if neg > pos else 0), False


def assess_news(query: str, lookback_days: int = _LOOKBACK_DAYS) -> NewsReport:
    """Fetch + score recent news for a company (or market-level query)."""
    rep = NewsReport(query=query)
    try:
        rep.headlines = fetch_headlines(query, lookback_days)
    except Exception as exc:
        logger.warning(f"News fetch failed for '{query}': {exc}")
        rep.reasons.append("news unavailable — scored neutral")
        return rep

    rep.headline_count = len(rep.headlines)
    if not rep.headlines:
        rep.reasons.append("no recent news — scored neutral")
        return rep

    total = 0
    for title in rep.headlines:
        score, red = _score_headline(title)
        total += score
        if red and not rep.red_flag:
            rep.red_flag = True
            rep.red_flag_headline = title
        if score > 0 and not rep.top_positive:
            rep.top_positive = title
        if score < 0 and not rep.top_negative:
            rep.top_negative = title

    rep.sentiment = round(max(-1.0, min(1.0, total / len(rep.headlines))), 2)

    if rep.red_flag:
        rep.reasons.append(f"RED FLAG in news: “{rep.red_flag_headline}”")
    elif rep.sentiment > 0.15:
        rep.reasons.append(f"news flow positive ({rep.headline_count} headlines)")
    elif rep.sentiment < -0.15:
        rep.reasons.append(f"news flow negative ({rep.headline_count} headlines)")
    else:
        rep.reasons.append(f"news flow neutral ({rep.headline_count} headlines)")
    return rep


def assess_stock_news(symbol: str, company_name: str = "") -> NewsReport:
    """News check for one stock — uses the company's real name when known."""
    name = company_name or symbol
    name = re.sub(r"\b(Limited|Ltd\.?)\b", "", name).strip()
    return assess_news(f'"{name}" stock')
