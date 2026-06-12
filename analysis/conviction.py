"""
Conviction engine — the agent's "head trader".

A technical signal is only an invitation to look closer. This layer combines:

    35%  technicals    (the chart setup that triggered the signal)
    25%  fundamentals  (is the company financially healthy?)
    20%  stock news    (any recent good/bad developments?)
    20%  market regime (is the overall market supportive?)

…into one composite conviction score, then sizes the bet accordingly:

    STRONG    (≥ 0.65)  → full position
    MODERATE  (≥ 0.50)  → 60% position
    SKIP      (< 0.50)  → no trade

Hard veto: a red-flag headline (fraud probe, raid, default…) blocks the
buy outright, whatever the score. Better to miss a winner than buy a scandal.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

from analysis.fundamentals import assess_fundamentals
from analysis.news import assess_stock_news
from analysis.regime import RegimeReport, assess_regime
from strategy.engine import Signal

W_TECHNICAL = 0.35
W_FUNDAMENTAL = 0.25
W_NEWS = 0.20
W_REGIME = 0.20

STRONG_THRESHOLD = 0.65
MODERATE_THRESHOLD = 0.50
MODERATE_SIZE = 0.6


@dataclass
class ConvictionReport:
    symbol: str
    verdict: str = "SKIP"            # STRONG | MODERATE | SKIP
    composite: float = 0.0           # 0 … 1
    size_multiplier: float = 0.0     # scales the risk officer's position size
    technical: float = 0.0
    fundamental: float = 0.5
    news: float = 0.5                # news sentiment mapped to 0…1
    regime: float = 0.5
    reasons: list[str] = field(default_factory=list)

    def as_audit_payload(self) -> dict:
        return {
            "symbol": self.symbol,
            "verdict": self.verdict,
            "composite": self.composite,
            "size_multiplier": self.size_multiplier,
            "scores": {
                "technical": self.technical,
                "fundamental": self.fundamental,
                "news": self.news,
                "regime": self.regime,
            },
            "reasons": self.reasons[:12],
        }


def assess(signal: Signal, regime: RegimeReport | None = None) -> ConvictionReport:
    """
    Full holistic assessment of one BUY signal.
    Pass a pre-computed RegimeReport when scanning many symbols (one fetch per scan).
    """
    rep = ConvictionReport(symbol=signal.symbol, technical=signal.confidence)
    rep.reasons.append(
        f"chart setup: {signal.strategy} with {signal.confidence:.0%} technical confidence, "
        f"risk/reward {signal.risk_reward}:1"
    )

    # ── Fundamentals ──
    fund = assess_fundamentals(signal.symbol)
    rep.fundamental = fund.score
    rep.reasons.extend(fund.reasons)

    # ── Stock-specific news ──
    news = assess_stock_news(signal.symbol, fund.company_name)
    rep.news = round(max(0.0, min(1.0, 0.5 + news.sentiment / 2)), 2)
    rep.reasons.extend(news.reasons)

    # ── Market regime (computed once per scan by the caller) ──
    if regime is None:
        regime = assess_regime()
    rep.regime = regime.score
    rep.reasons.append(f"market regime: {regime.label} ({regime.score:.0%})")

    # ── Composite ──
    rep.composite = round(
        W_TECHNICAL * rep.technical
        + W_FUNDAMENTAL * rep.fundamental
        + W_NEWS * rep.news
        + W_REGIME * rep.regime,
        3,
    )

    # ── Verdict ──
    if news.red_flag:
        rep.verdict, rep.size_multiplier = "SKIP", 0.0
        rep.reasons.append("VETO: serious negative news — no buy regardless of score")
    elif rep.composite >= STRONG_THRESHOLD:
        rep.verdict, rep.size_multiplier = "STRONG", 1.0
    elif rep.composite >= MODERATE_THRESHOLD:
        rep.verdict, rep.size_multiplier = "MODERATE", MODERATE_SIZE
    else:
        rep.verdict, rep.size_multiplier = "SKIP", 0.0
        rep.reasons.append("conviction too low — passing on this one")

    logger.info(
        f"Conviction {signal.symbol}: {rep.verdict} composite={rep.composite:.2f} "
        f"(tech={rep.technical:.2f} fund={rep.fundamental:.2f} "
        f"news={rep.news:.2f} regime={rep.regime:.2f})"
    )
    return rep
