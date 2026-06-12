"""
Fundamentals layer — company quality check via Yahoo Finance.

Answers one question: "is this a financially healthy company worth holding
for a swing trade?" Data changes slowly, so results are cached for a day.
Missing data is scored neutral (0.5) — never punish a stock for Yahoo gaps.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "fundamentals_cache.json"
_CACHE_TTL_SECS = 24 * 3600

# Yahoo needs the .NS suffix for NSE symbols (works for M&M.NS, BAJAJ-AUTO.NS too)
def _yahoo_symbol(symbol: str) -> str:
    return f"{symbol}.NS"


@dataclass
class FundamentalReport:
    symbol: str
    score: float = 0.5            # 0 (poor) … 1 (excellent), 0.5 = neutral/unknown
    company_name: str = ""
    sector: str = ""
    reasons: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


def _load_cache() -> dict:
    try:
        cache = json.loads(_CACHE_PATH.read_text())
        if time.time() - cache.get("_fetched_at", 0) < _CACHE_TTL_SECS:
            return cache
    except (OSError, ValueError):
        pass
    return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(cache))
    except OSError as exc:
        logger.warning(f"Fundamentals cache write failed: {exc}")


def _fetch_info(symbol: str) -> dict:
    import yfinance as yf  # imported lazily — only needed at scan time

    info = yf.Ticker(_yahoo_symbol(symbol)).info or {}
    keep = (
        "longName", "sector", "trailingPE", "forwardPE", "returnOnEquity",
        "debtToEquity", "profitMargins", "earningsQuarterlyGrowth",
        "revenueGrowth", "marketCap", "heldPercentInsiders",
    )
    return {k: info.get(k) for k in keep}


def get_fundamentals(symbol: str) -> dict:
    """Fetch (or reuse today's cached) fundamental data for one symbol."""
    cache = _load_cache()
    if symbol in cache:
        return cache[symbol]

    try:
        data = _fetch_info(symbol)
    except Exception as exc:
        logger.warning(f"Fundamentals fetch failed for {symbol}: {exc}")
        data = {}

    cache.setdefault("_fetched_at", time.time())
    cache[symbol] = data
    _save_cache(cache)
    return data


def assess_fundamentals(symbol: str) -> FundamentalReport:
    """Score company quality 0–1 with plain-language reasons."""
    d = get_fundamentals(symbol)
    rep = FundamentalReport(
        symbol=symbol,
        company_name=d.get("longName") or symbol,
        sector=d.get("sector") or "",
        data=d,
    )
    if not d or all(v is None for k, v in d.items() if k not in ("longName", "sector")):
        rep.reasons.append("fundamental data unavailable — scored neutral")
        return rep

    checks: list[tuple[bool | None, str, str]] = []  # (good, good_text, bad_text)

    roe = d.get("returnOnEquity")
    if roe is not None:
        checks.append((roe >= 0.12,
                       f"strong return on equity ({roe:.0%})",
                       f"weak return on equity ({roe:.0%})"))

    # Banks/NBFCs run on leverage — debt ratio is not meaningful for them
    dte = d.get("debtToEquity")
    if dte is not None and rep.sector != "Financial Services":
        checks.append((dte < 100,  # yfinance reports D/E in percent
                       "manageable debt levels",
                       f"heavy debt load (D/E {dte/100:.1f}x)"))

    pe = d.get("trailingPE")
    if pe is not None:
        checks.append((0 < pe < 65,
                       f"reasonable valuation (PE {pe:.0f})",
                       f"stretched valuation (PE {pe:.0f})" if pe > 0 else "loss-making (negative PE)"))

    eg = d.get("earningsQuarterlyGrowth")
    if eg is not None:
        checks.append((eg > 0,
                       f"profits growing ({eg:+.0%} YoY)",
                       f"profits shrinking ({eg:+.0%} YoY)"))

    pm = d.get("profitMargins")
    if pm is not None:
        checks.append((pm > 0.05,
                       f"healthy profit margins ({pm:.0%})",
                       f"thin margins ({pm:.0%})"))

    if not checks:
        rep.reasons.append("fundamental data unavailable — scored neutral")
        return rep

    passed = sum(1 for good, *_ in checks if good)
    rep.score = round(passed / len(checks), 2)
    rep.reasons = [good_txt if good else bad_txt for good, good_txt, bad_txt in checks]
    return rep
