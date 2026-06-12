"""
Verification — analysis layers (fundamentals, news, regime, conviction).

Run:  python -m tests.test_analysis
Uses live Yahoo Finance + Google News (like the other layer tests use live APIs).
"""
from __future__ import annotations

import sys

from loguru import logger

from analysis.conviction import assess, MODERATE_SIZE
from analysis.fundamentals import assess_fundamentals
from analysis.news import NewsReport, _score_headline, assess_stock_news
from analysis.regime import assess_regime
from strategy.engine import Signal
from strategy.universe import NIFTY100


def _fake_signal(symbol: str = "RELIANCE", confidence: float = 0.8) -> Signal:
    return Signal(
        symbol=symbol, exchange="NSE", direction="BUY", strategy="ema_crossover",
        price_at_signal=1300.0, entry_price=1300.0,
        stop_loss=1250.0, target=1375.0, atr=25.0, rsi=45.0,
        vol_ratio=1.5, confidence=confidence,
    )


def main() -> None:
    print("\nTest 1 — Universe is Nifty 100")
    assert len(NIFTY100) == 100, f"expected 100 symbols, got {len(NIFTY100)}"
    assert len(set(NIFTY100)) == 100, "duplicate symbols in NIFTY100"
    print(f"  ✓ {len(NIFTY100)} unique symbols")

    print("\nTest 2 — Fundamentals (live Yahoo Finance)")
    rep = assess_fundamentals("RELIANCE")
    assert 0.0 <= rep.score <= 1.0
    assert rep.reasons, "no reasons produced"
    print(f"  ✓ RELIANCE → {rep.company_name} | score={rep.score} | {rep.reasons[0]}")
    rep2 = assess_fundamentals("RELIANCE")  # second call must hit the cache
    assert rep2.score == rep.score
    print("  ✓ daily cache works")

    print("\nTest 3 — Headline lexicon")
    s, red = _score_headline("Reliance profit jumps 20% — beats estimates")
    assert s > 0 and not red, "positive headline mis-scored"
    s, red = _score_headline("Company shares plunge after weak results")
    assert s < 0 and not red, "negative headline mis-scored"
    s, red = _score_headline("SEBI opens fraud probe into the company")
    assert red, "red flag not detected"
    s, red = _score_headline("ICICI Bank pips Reliance to become second-largest Nifty stock")
    assert not red, "'Bank' must not trigger the 'ban' red flag (word boundaries)"
    s, red = _score_headline("RBI bans the lender from issuing new credit cards")
    assert red, "'bans' red flag not detected"
    print("  ✓ positive / negative / red-flag / word-boundary headlines scored correctly")

    print("\nTest 4 — Stock news (live Google News RSS)")
    news = assess_stock_news("RELIANCE", "Reliance Industries Limited")
    assert isinstance(news, NewsReport)
    assert -1.0 <= news.sentiment <= 1.0
    print(f"  ✓ {news.headline_count} headlines | sentiment={news.sentiment:+.2f} | {news.reasons[0]}")

    print("\nTest 5 — Market regime (live)")
    regime = assess_regime()
    assert 0.0 <= regime.score <= 1.0
    assert regime.label in ("BULLISH", "NEUTRAL", "BEARISH")
    print(f"  ✓ regime={regime.label} score={regime.score}")
    for r in regime.reasons[:4]:
        print(f"      · {r}")

    print("\nTest 6 — Conviction verdicts")
    report = assess(_fake_signal(confidence=0.85), regime)
    assert report.verdict in ("STRONG", "MODERATE", "SKIP")
    assert report.size_multiplier in (0.0, MODERATE_SIZE, 1.0)
    expected = (report.composite >= 0.65 and report.size_multiplier == 1.0) or \
               (0.50 <= report.composite < 0.65 and report.size_multiplier == MODERATE_SIZE) or \
               (report.composite < 0.50 and report.size_multiplier == 0.0) or \
               report.verdict == "SKIP"  # red-flag veto path
    assert expected, "verdict/multiplier inconsistent with composite"
    print(f"  ✓ RELIANCE: {report.verdict} composite={report.composite} size×{report.size_multiplier}")
    for r in report.reasons[:5]:
        print(f"      · {r}")

    print("\nTest 7 — Risk officer scales by conviction")
    from risk.capital import CapitalState
    from risk.officer import evaluate
    state = CapitalState(total_capital=100_000, available_cash=100_000)
    full = evaluate(_fake_signal(), state, size_multiplier=1.0)
    small = evaluate(_fake_signal(), state, size_multiplier=0.6)
    zero = evaluate(_fake_signal(), state, size_multiplier=0.0)
    assert full.approved and small.approved
    assert small.quantity < full.quantity
    assert not zero.approved
    print(f"  ✓ qty full={full.quantity} vs moderate={small.quantity} vs zero=rejected")

    logger.success("Analysis layer test passed — holistic conviction engine working.")


if __name__ == "__main__":
    sys.exit(main())
