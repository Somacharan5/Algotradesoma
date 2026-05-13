"""
Day 7 success-criteria test:
  1. Open a paper trade → log_signal + log_risk + log_compliance audit events
  2. Close trade → daily_pnl rollup written to Supabase
  3. Simulate circuit-breaker threshold → enforce_circuit_breaker fires,
     all open positions emergency-closed, circuit_tripped=True in daily_pnl
  4. EOD summary built correctly

Run with:  python -m tests.test_auditor
"""
from __future__ import annotations

import time
from datetime import datetime

from audit.auditor import (
    log_signal, log_risk_decision, log_compliance_decision,
    rollup_daily_pnl, enforce_circuit_breaker, end_of_day_summary,
)
from db.client import db_fetch
from execution.executor import open_trade, close_trade, _open_trades
from risk.capital import reset_state
from risk.officer import RiskDecision
from strategy.engine import Signal
from loguru import logger


def make_signal(symbol: str = "SBIN", entry: float = 970.10) -> Signal:
    atr = 18.5
    return Signal(
        symbol=symbol, exchange="NSE",
        direction="BUY", strategy="ema_crossover",
        price_at_signal=entry, entry_price=entry,
        stop_loss=round(entry - 2 * atr, 2),
        target=round(entry + 3 * atr, 2),
        atr=atr, rsi=48.0, vol_ratio=1.8, confidence=0.75,
        timestamp=datetime.now(),
    )


def make_decision(signal: Signal, qty: int = 5) -> RiskDecision:
    stop_dist = signal.entry_price - signal.stop_loss
    return RiskDecision(
        approved=True, signal=signal, quantity=qty,
        capital_at_risk=qty * stop_dist,
        trade_value=qty * signal.entry_price,
    )


def main() -> None:
    print("\n── Day 7: Auditor Tests ──\n")

    _open_trades.clear()
    state = reset_state()

    # ── Test 1: Audit log entries for all 3 layers ─────────────────────────
    print("Test 1 — Audit log: signal + risk + compliance events")
    sig = make_signal()

    log_signal(sig.symbol, sig.direction, sig.strategy, sig.confidence)
    log_risk_decision(sig.symbol, approved=True, qty=5)
    log_compliance_decision(sig.symbol, passed=True)

    time.sleep(0.5)
    rows = db_fetch("audit_log", {"layer": "strategy"})
    strategy_events = [r["event"] for r in rows]
    assert "signal_generated" in strategy_events, f"missing signal_generated: {strategy_events}"
    print(f"  ✓ strategy.signal_generated logged")

    rows = db_fetch("audit_log", {"layer": "risk"})
    assert any(r["event"] == "risk_evaluated" for r in rows)
    print(f"  ✓ risk.risk_evaluated logged")

    rows = db_fetch("audit_log", {"layer": "compliance"})
    assert any(r["event"] == "compliance_checked" for r in rows)
    print(f"  ✓ compliance.compliance_checked logged")

    # ── Test 2: Daily PnL rollup after a closed trade ──────────────────────
    print("\nTest 2 — daily_pnl rollup after trade close")
    dec = make_decision(sig)
    trade = open_trade(dec)
    close_trade(trade, exit_price=sig.target, reason="target_hit")  # profit

    row = rollup_daily_pnl(state)
    assert row["realised_pnl"] > 0, f"expected profit in rollup: {row}"
    print(f"  ✓ daily_pnl row written: realised=₹{row['realised_pnl']:+,.2f}  "
          f"circuit_tripped={row['circuit_tripped']}")

    time.sleep(0.5)
    db_rows = db_fetch("daily_pnl", {"trade_date": row["trade_date"]})
    assert db_rows, "daily_pnl row not found in Supabase"
    assert float(db_rows[0]["realised_pnl"]) == row["realised_pnl"]
    print(f"  ✓ Supabase daily_pnl confirmed: {db_rows[0]['trade_date']}  "
          f"realised=₹{db_rows[0]['realised_pnl']}")

    # ── Test 3: Circuit breaker enforcement ───────────────────────────────
    print("\nTest 3 — Circuit breaker enforcement")
    _open_trades.clear()
    state2 = reset_state()

    # Open two trades
    sig2 = make_signal("AXISBANK", entry=1255.70)
    sig3 = make_signal("LT", entry=3915.80)
    dec2 = make_decision(sig2, qty=3)
    dec3 = make_decision(sig3, qty=1)
    trade2 = open_trade(dec2)
    trade3 = open_trade(dec3)

    assert len(_open_trades) == 2
    print(f"  Opened 2 trades: {[t.symbol for t in _open_trades.values()]}")

    # Simulate -3.6% daily loss (beyond 3.5% circuit limit)
    state2.daily_realised_pnl = -state2.total_capital * 0.036
    print(f"  Simulated daily loss: ₹{state2.daily_realised_pnl:,.0f} "
          f"({state2.daily_loss_pct:.2%})")

    tripped = enforce_circuit_breaker(state2)

    assert tripped, "circuit breaker should have fired"
    assert state2.circuit_tripped, "state.circuit_tripped should be True"
    assert len(_open_trades) == 0, f"all positions should be closed, got {len(_open_trades)}"
    print(f"  ✓ Circuit breaker fired — all positions emergency-closed")
    print(f"  ✓ Open trades remaining: {len(_open_trades)}")

    time.sleep(0.5)
    audit_rows = db_fetch("audit_log", {"layer": "auditor"})
    cb_events = [r for r in audit_rows if r["event"] == "circuit_breaker_tripped"]
    assert cb_events, "circuit_breaker_tripped not in audit_log"
    print(f"  ✓ audit_log: circuit_breaker_tripped event recorded")

    # Second call should NOT fire again
    tripped2 = enforce_circuit_breaker(state2)
    assert not tripped2, "circuit breaker should not double-fire"
    print(f"  ✓ Second enforce call correctly skipped (already tripped)")

    # ── Test 4: EOD summary ────────────────────────────────────────────────
    print("\nTest 4 — End-of-day summary")
    _open_trades.clear()
    state3 = reset_state()
    state3.daily_realised_pnl = 1_250.0
    state3.daily_unrealised_pnl = -320.0
    state3.open_positions_count = 1

    summary = end_of_day_summary(state3)
    assert summary["realised_pnl"] == 1_250.0
    assert summary["unrealised_pnl"] == -320.0
    assert "date" in summary
    print(f"  ✓ EOD summary built:")
    for k, v in summary.items():
        print(f"     {k:<20}: {v}")

    print("\n── All 4 auditor tests passed ──")
    logger.success("Day 7 test passed — Auditor working correctly.")


if __name__ == "__main__":
    main()
