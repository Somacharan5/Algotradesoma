"""
Market regime layer — "is this a good week to be buying stocks at all?"

Checks, all via Yahoo Finance (free):
  - Nifty 50 trend: index above/below its 50-day average, 5-day momentum
  - India VIX: market fear gauge (high = nervous market)
  - S&P 500 5-day move: global risk appetite (India gaps with the world)
  - Indian & global market news sentiment

A great stock setup in a falling market usually fails — this layer makes
the agent buy smaller (or not at all) when the tide is going out.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

from analysis.news import assess_news


@dataclass
class RegimeReport:
    score: float = 0.5            # 0 (hostile) … 1 (favourable)
    label: str = "NEUTRAL"        # BULLISH | NEUTRAL | BEARISH
    reasons: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


def _history(ticker: str, period: str = "4mo"):
    import yfinance as yf  # lazy import

    df = yf.Ticker(ticker).history(period=period)
    return df if df is not None and not df.empty else None


def assess_regime() -> RegimeReport:
    """Gather market-level context. Called once per scan, not per stock."""
    rep = RegimeReport()
    points: list[float] = []

    # ── Nifty 50 trend ──
    try:
        nifty = _history("^NSEI")
        close = nifty["Close"]
        dma50 = close.rolling(50).mean().iloc[-1]
        last = close.iloc[-1]
        ret5 = (last / close.iloc[-6] - 1) * 100
        rep.data["nifty"] = round(last, 1)
        rep.data["nifty_ret5d_pct"] = round(ret5, 2)
        if last > dma50:
            points.append(1.0)
            rep.reasons.append("Nifty is trading above its 50-day average (uptrend)")
        else:
            points.append(0.0)
            rep.reasons.append("Nifty is below its 50-day average (downtrend)")
        if ret5 <= -2.5:
            points.append(0.0)
            rep.reasons.append(f"Indian market fell {ret5:.1f}% in the last 5 days")
        elif ret5 >= 1.5:
            points.append(1.0)
            rep.reasons.append(f"Indian market up {ret5:+.1f}% in the last 5 days")
        else:
            points.append(0.5)
    except Exception as exc:
        logger.warning(f"Regime: Nifty check failed — {exc}")

    # ── India VIX (fear index) ──
    try:
        vix_df = _history("^INDIAVIX", period="1mo")
        if vix_df is not None:
            vix = float(vix_df["Close"].iloc[-1])
            rep.data["india_vix"] = round(vix, 1)
            if vix >= 20:
                points.append(0.0)
                rep.reasons.append(f"India VIX is high at {vix:.0f} — market is nervous")
            elif vix <= 14:
                points.append(1.0)
                rep.reasons.append(f"India VIX is calm at {vix:.0f}")
            else:
                points.append(0.5)
    except Exception as exc:
        logger.warning(f"Regime: VIX check failed — {exc}")

    # ── Global cue: S&P 500 5-day ──
    try:
        spx = _history("^GSPC", period="1mo")
        sret5 = (spx["Close"].iloc[-1] / spx["Close"].iloc[-6] - 1) * 100
        rep.data["sp500_ret5d_pct"] = round(sret5, 2)
        if sret5 <= -3:
            points.append(0.0)
            rep.reasons.append(f"Global markets weak — S&P 500 down {sret5:.1f}% in 5 days")
        elif sret5 >= 1:
            points.append(1.0)
            rep.reasons.append(f"Global markets supportive — S&P 500 up {sret5:+.1f}% in 5 days")
        else:
            points.append(0.5)
    except Exception as exc:
        logger.warning(f"Regime: S&P 500 check failed — {exc}")

    # ── Market-level news (India + global) ──
    try:
        india_news = assess_news("Indian stock market Nifty Sensex", lookback_days=3)
        global_news = assess_news("global markets Fed rates crude oil", lookback_days=3)
        avg = (india_news.sentiment + global_news.sentiment) / 2
        rep.data["news_sentiment"] = round(avg, 2)
        points.append(max(0.0, min(1.0, 0.5 + avg)))
        if avg < -0.2:
            rep.reasons.append("market news flow is negative (India + global)")
        elif avg > 0.2:
            rep.reasons.append("market news flow is positive (India + global)")
    except Exception as exc:
        logger.warning(f"Regime: market news failed — {exc}")

    if points:
        rep.score = round(sum(points) / len(points), 2)
    else:
        rep.reasons.append("market data unavailable — scored neutral")

    rep.label = "BULLISH" if rep.score >= 0.65 else "BEARISH" if rep.score <= 0.35 else "NEUTRAL"
    return rep
