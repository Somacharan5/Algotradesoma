from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
import pytz
from loguru import logger

from compliance.holidays import is_holiday
from strategy.engine import Signal

# ── Constants ──────────────────────────────────────────────────────────────

IST = pytz.timezone("Asia/Kolkata")

MARKET_OPEN  = time(9, 15)
MARKET_CLOSE = time(15, 30)

# Minimum average daily volume to trade a stock (liquidity filter)
MIN_AVG_VOLUME = 500_000

# Price band — ignore penny stocks and ultra-HNI instruments
MIN_PRICE = 50.0
MAX_PRICE = 50_000.0


# ── Decision ───────────────────────────────────────────────────────────────

@dataclass
class ComplianceDecision:
    passed: bool
    signal: Signal
    rejection_reason: str = ""

    def __str__(self) -> str:
        if self.passed:
            return f"PASSED  {self.signal.symbol}"
        return f"BLOCKED  {self.signal.symbol}  reason='{self.rejection_reason}'"


# ── Open positions registry ────────────────────────────────────────────────
# Simple in-memory set; Day 6 Executor will populate/clear this.

_open_symbols: set[str] = set()


def register_open(symbol: str) -> None:
    _open_symbols.add(symbol)


def deregister_open(symbol: str) -> None:
    _open_symbols.discard(symbol)


def open_symbols() -> set[str]:
    return set(_open_symbols)


# ── Internal helpers ───────────────────────────────────────────────────────

def _block(signal: Signal, reason: str) -> ComplianceDecision:
    logger.warning(f"Compliance BLOCK {signal.symbol}: {reason}")
    return ComplianceDecision(passed=False, signal=signal, rejection_reason=reason)


def _now_ist() -> datetime:
    return datetime.now(IST)


# ── Checks ─────────────────────────────────────────────────────────────────

def check_market_hours(signal: Signal, now: datetime | None = None) -> ComplianceDecision:
    """Block signals outside NSE trading hours (9:15–15:30 IST, Mon–Fri)."""
    dt = now or _now_ist()
    current_time = dt.time()
    weekday = dt.weekday()   # 0=Mon … 6=Sun

    if weekday >= 5:
        return _block(signal, f"market closed — weekend ({dt.strftime('%A')})")

    if is_holiday(dt.date()):
        return _block(signal, f"NSE holiday on {dt.date()}")

    if not (MARKET_OPEN <= current_time <= MARKET_CLOSE):
        return _block(
            signal,
            f"outside trading hours ({current_time.strftime('%H:%M')} IST; "
            f"window is 09:15–15:30)",
        )

    return ComplianceDecision(passed=True, signal=signal)


def check_duplicate_position(signal: Signal) -> ComplianceDecision:
    """Block if we already hold this symbol."""
    if signal.symbol in _open_symbols:
        return _block(
            signal,
            f"already holding {signal.symbol} — no pyramiding allowed",
        )
    return ComplianceDecision(passed=True, signal=signal)


def check_price_band(signal: Signal) -> ComplianceDecision:
    """Block penny stocks and ultra-expensive instruments."""
    price = signal.entry_price
    if price < MIN_PRICE:
        return _block(signal, f"price ₹{price:.2f} below minimum ₹{MIN_PRICE:.0f} (penny stock)")
    if price > MAX_PRICE:
        return _block(signal, f"price ₹{price:.2f} above maximum ₹{MAX_PRICE:,.0f}")
    return ComplianceDecision(passed=True, signal=signal)


def check_liquidity(signal: Signal, avg_volume: float) -> ComplianceDecision:
    """Block stocks with insufficient average daily volume."""
    if avg_volume < MIN_AVG_VOLUME:
        return _block(
            signal,
            f"avg volume {avg_volume:,.0f} below minimum {MIN_AVG_VOLUME:,}",
        )
    return ComplianceDecision(passed=True, signal=signal)


# ── Full gate ──────────────────────────────────────────────────────────────

def run_all(
    signal: Signal,
    avg_volume: float = 1_000_000,
    now: datetime | None = None,
    bypass_hours: bool = False,
) -> ComplianceDecision:
    """
    Run every compliance check in order.
    Returns the first failure, or a passing decision if all pass.

    bypass_hours=True skips the market-hours check (used in tests / after-hours scanning).
    """
    checks = []

    if not bypass_hours:
        checks.append(check_market_hours(signal, now))

    checks += [
        check_duplicate_position(signal),
        check_price_band(signal),
        check_liquidity(signal, avg_volume),
    ]

    for decision in checks:
        if not decision.passed:
            return decision

    logger.info(f"Compliance PASSED {signal.symbol}")
    return ComplianceDecision(passed=True, signal=signal)
