"""
Day 5 success-criteria test:
  Verify all compliance gates fire correctly — no broker connection needed.

  ✓ Signal inside market hours → PASSED
  ✓ Signal on weekend → BLOCKED
  ✓ Signal before market open → BLOCKED
  ✓ Signal after market close → BLOCKED
  ✓ NSE holiday → BLOCKED
  ✓ Duplicate position → BLOCKED
  ✓ Penny stock (price < ₹50) → BLOCKED
  ✓ Illiquid stock (volume < 5L) → BLOCKED
  ✓ All checks passing → PASSED (full gate)

Run with:  python -m tests.test_compliance
"""
from __future__ import annotations

from datetime import datetime, date
import pytz

from compliance import checker
from compliance.checker import (
    run_all, check_market_hours, check_duplicate_position,
    check_price_band, check_liquidity,
    register_open, deregister_open,
    ComplianceDecision,
)
from compliance.holidays import is_holiday
from strategy.engine import Signal
from loguru import logger

IST = pytz.timezone("Asia/Kolkata")


def make_signal(symbol: str = "RELIANCE", entry: float = 1358.80) -> Signal:
    return Signal(
        symbol=symbol, exchange="NSE",
        direction="BUY", strategy="ema_crossover",
        price_at_signal=entry, entry_price=entry,
        stop_loss=entry - 65.0, target=entry + 98.0,
        atr=32.68, rsi=45.0, vol_ratio=1.6, confidence=0.72,
    )


def ist(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return IST.localize(datetime(year, month, day, hour, minute))


def assert_passed(dec: ComplianceDecision, label: str) -> None:
    assert dec.passed, f"[FAIL] {label}: expected PASSED, got BLOCKED — {dec.rejection_reason}"
    print(f"  ✓ {label}: {dec}")


def assert_blocked(dec: ComplianceDecision, label: str, contains: str = "") -> None:
    assert not dec.passed, f"[FAIL] {label}: expected BLOCKED, got PASSED"
    if contains:
        assert contains in dec.rejection_reason, (
            f"[FAIL] {label}: reason missing '{contains}' — got '{dec.rejection_reason}'"
        )
    print(f"  ✓ {label}: {dec}")


def main() -> None:
    sig = make_signal()
    print("\n── Day 5: Compliance Checker Tests ──\n")

    # ── Market hours ───────────────────────────────────────────────────────
    print("Market Hours Gate")

    dec = check_market_hours(sig, now=ist(2026, 5, 13, 10, 30))   # Wed 10:30 IST
    assert_passed(dec, "weekday inside hours")

    dec = check_market_hours(sig, now=ist(2026, 5, 16, 10, 30))   # Saturday
    assert_blocked(dec, "Saturday", "weekend")

    dec = check_market_hours(sig, now=ist(2026, 5, 17, 10, 30))   # Sunday
    assert_blocked(dec, "Sunday", "weekend")

    dec = check_market_hours(sig, now=ist(2026, 5, 13, 8, 0))     # 08:00 — before open
    assert_blocked(dec, "before market open", "outside trading hours")

    dec = check_market_hours(sig, now=ist(2026, 5, 13, 16, 0))    # 16:00 — after close
    assert_blocked(dec, "after market close", "outside trading hours")

    dec = check_market_hours(sig, now=ist(2026, 1, 26, 10, 30))   # Republic Day (Monday)
    assert_blocked(dec, "NSE holiday (Jan 26)", "NSE holiday")

    # ── Duplicate position ─────────────────────────────────────────────────
    print("\nDuplicate Position Gate")

    checker._open_symbols.clear()
    dec = check_duplicate_position(sig)
    assert_passed(dec, "no existing position")

    register_open("RELIANCE")
    dec = check_duplicate_position(sig)
    assert_blocked(dec, "already holding RELIANCE", "already holding")
    deregister_open("RELIANCE")

    dec = check_duplicate_position(sig)
    assert_passed(dec, "after deregister")

    # ── Price band ─────────────────────────────────────────────────────────
    print("\nPrice Band Gate")

    dec = check_price_band(make_signal(entry=1358.80))
    assert_passed(dec, "normal price ₹1358")

    dec = check_price_band(make_signal(entry=12.50))
    assert_blocked(dec, "penny stock ₹12.50", "penny stock")

    dec = check_price_band(make_signal(entry=75_000.0))
    assert_blocked(dec, "above max ₹75,000", "above maximum")

    # ── Liquidity ──────────────────────────────────────────────────────────
    print("\nLiquidity Gate")

    dec = check_liquidity(sig, avg_volume=15_000_000)
    assert_passed(dec, "high volume stock")

    dec = check_liquidity(sig, avg_volume=200_000)
    assert_blocked(dec, "illiquid stock 2L volume", "avg volume")

    # ── Full gate ──────────────────────────────────────────────────────────
    print("\nFull Gate (run_all)")

    checker._open_symbols.clear()
    dec = run_all(
        sig, avg_volume=15_000_000,
        now=ist(2026, 5, 13, 10, 30),   # valid trading window
    )
    assert_passed(dec, "all checks passing")

    # Weekend short-circuits immediately
    dec = run_all(sig, now=ist(2026, 5, 16, 10, 30))
    assert_blocked(dec, "full gate — weekend short-circuit", "weekend")

    # bypass_hours for after-hours scanning
    dec = run_all(
        sig, avg_volume=15_000_000,
        now=ist(2026, 5, 13, 20, 0),    # 20:00 IST — after close
        bypass_hours=True,
    )
    assert_passed(dec, "bypass_hours for after-hours scan")

    # ── Holiday helper ─────────────────────────────────────────────────────
    print("\nHoliday Helper")
    assert is_holiday(date(2026, 1, 26)), "Jan 26 (Republic Day) should be holiday"
    assert not is_holiday(date(2026, 5, 13)), "May 13 should not be holiday"
    print("  ✓ is_holiday(Jan 26 2026 — Republic Day) = True")
    print("  ✓ is_holiday(May 13 2026) = False")

    print("\n── All compliance tests passed ──")
    logger.success("Day 5 test passed — Compliance layer working correctly.")


if __name__ == "__main__":
    main()
