"""
Day 4 success-criteria test:
  Feed synthetic signals through the Risk Officer and verify:
    ✓ Normal signal → APPROVED with correct qty
    ✓ Circuit breaker tripped → REJECTED
    ✓ Max positions reached → REJECTED
    ✓ Insufficient cash → REJECTED
    ✓ Concentration cap enforced (20% single-name limit)

Run with:  python -m tests.test_risk
"""
from __future__ import annotations

from datetime import datetime
from risk.capital import reset_state
from risk.officer import evaluate, RiskDecision
from strategy.engine import Signal
from loguru import logger


def make_signal(
    symbol: str = "RELIANCE",
    entry: float = 1358.80,
    sl: float = 1293.44,     # entry - 2×ATR (ATR=32.68)
    target: float = 1457.84, # entry + 3×ATR
    atr: float = 32.68,
    rsi: float = 45.0,
) -> Signal:
    return Signal(
        symbol=symbol, exchange="NSE",
        direction="BUY", strategy="ema_crossover",
        price_at_signal=entry, entry_price=entry,
        stop_loss=sl, target=target,
        atr=atr, rsi=rsi, vol_ratio=1.6,
        confidence=0.72, timestamp=datetime.now(),
    )


def assert_approved(dec: RiskDecision, label: str) -> None:
    assert dec.approved, f"[FAIL] {label}: expected APPROVED, got REJECTED — {dec.rejection_reason}"
    print(f"  ✓ {label}: {dec}")


def assert_rejected(dec: RiskDecision, label: str, reason_contains: str = "") -> None:
    assert not dec.approved, f"[FAIL] {label}: expected REJECTED, got APPROVED"
    if reason_contains:
        assert reason_contains in dec.rejection_reason, (
            f"[FAIL] {label}: expected reason containing '{reason_contains}', "
            f"got '{dec.rejection_reason}'"
        )
    print(f"  ✓ {label}: {dec}")


def main() -> None:
    print("\n── Day 4: Risk Officer Tests ──\n")

    # ── Test 1: Normal approval ────────────────────────────────────────────
    print("Test 1 — Normal BUY signal approval")
    state = reset_state()   # fresh ₹1,00,000 paper capital
    sig = make_signal()
    dec = evaluate(sig, state)
    assert_approved(dec, "normal BUY")

    stop_dist = sig.entry_price - sig.stop_loss
    # 1% risk = ₹1,000 → raw qty=15, but 20% cap (₹20,000 / ₹1358.80) = 14 shares
    import math
    raw_qty = int(1_000 / stop_dist)
    capped_qty = max(1, math.floor(state.total_capital * 0.20 / sig.entry_price))
    expected_qty = min(raw_qty, capped_qty)
    assert dec.quantity == expected_qty, f"qty mismatch: got {dec.quantity}, want {expected_qty}"
    print(f"     stop_distance=₹{stop_dist:.2f}  risk_budget=₹1,000  raw_qty={raw_qty}  capped_qty={capped_qty}  final_qty={dec.quantity}")
    print(f"     trade_value=₹{dec.trade_value:,.0f}  capital_at_risk=₹{dec.capital_at_risk:,.0f}")

    # ── Test 2: Circuit breaker ────────────────────────────────────────────
    print("\nTest 2 — Circuit breaker blocks new trades")
    state = reset_state()
    state.daily_realised_pnl = -3_600.0   # -3.6% of ₹1,00,000 > 3.5% limit
    dec = evaluate(make_signal(), state)
    assert_rejected(dec, "circuit breaker", "circuit breaker")

    # ── Test 3: Max open positions ─────────────────────────────────────────
    print("\nTest 3 — Max positions guard")
    state = reset_state()
    state.open_positions_count = 5    # at the limit
    dec = evaluate(make_signal(), state)
    assert_rejected(dec, "max positions", "max open positions")

    # ── Test 4: Insufficient cash ──────────────────────────────────────────
    print("\nTest 4 — Insufficient cash")
    state = reset_state()
    state.available_cash = 500.0   # only ₹500 left
    dec = evaluate(make_signal(), state)
    assert_rejected(dec, "insufficient cash", "insufficient cash")

    # ── Test 5: Concentration cap ──────────────────────────────────────────
    print("\nTest 5 — 20% concentration cap on expensive stock")
    state = reset_state()
    # LT at ₹3915 — uncapped qty would be ~15 shares × ₹3915 = ₹58,725 (>20%)
    sig_lt = make_signal(symbol="LT", entry=3915.80, sl=3837.04, target=4033.04, atr=39.38)
    dec = evaluate(sig_lt, state)
    assert_approved(dec, "LT concentration cap")
    max_value = state.total_capital * 0.20
    assert dec.trade_value <= max_value + sig_lt.entry_price, (
        f"concentration cap violated: ₹{dec.trade_value:,.0f} > ₹{max_value:,.0f}"
    )
    print(f"     qty capped at {dec.quantity}  value=₹{dec.trade_value:,.0f}  "
          f"(20% cap=₹{max_value:,.0f})")

    # ── Test 6: Zero stop distance ─────────────────────────────────────────
    print("\nTest 6 — Malformed signal (SL == entry)")
    state = reset_state()
    bad_sig = make_signal(sl=1358.80)   # stop == entry
    dec = evaluate(bad_sig, state)
    assert_rejected(dec, "zero stop distance", "zero")

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"\n── Capital state after normal trade ──")
    state = reset_state()
    sig = make_signal()
    dec = evaluate(sig, state)
    state.reserve(dec.trade_value)
    print(f"  {state.summary()}")

    print("\n── All 6 risk tests passed ──")
    logger.success("Day 4 test passed — Risk Officer working correctly.")


if __name__ == "__main__":
    main()
