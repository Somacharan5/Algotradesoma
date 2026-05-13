"""
Day 6 success-criteria test:
  1. Paper-open a RELIANCE trade
  2. Verify row exists in Supabase trades + positions tables
  3. Simulate stop-loss hit → close trade
  4. Verify trades row updated + capital released + PnL logged
  5. Simulate target hit on a second trade → verify profit

Run with:  python -m tests.test_execution
Requires:  SUPABASE_URL and SUPABASE_SERVICE_KEY in .env
"""
from __future__ import annotations

import time
from datetime import datetime

from db.client import db_fetch
from execution.executor import (
    open_trade, close_trade, get_open_trades,
    load_open_trades_from_db, _open_trades,
)
from risk.capital import reset_state
from risk.officer import RiskDecision
from strategy.engine import Signal
from loguru import logger


def make_signal(symbol: str = "RELIANCE", entry: float = 1358.80) -> Signal:
    atr = 32.68
    return Signal(
        symbol=symbol, exchange="NSE",
        direction="BUY", strategy="ema_crossover",
        price_at_signal=entry, entry_price=entry,
        stop_loss=round(entry - 2 * atr, 2),
        target=round(entry + 3 * atr, 2),
        atr=atr, rsi=45.0, vol_ratio=1.6, confidence=0.72,
        timestamp=datetime.now(),
    )


def make_decision(signal: Signal, qty: int = 14) -> RiskDecision:
    stop_dist = signal.entry_price - signal.stop_loss
    return RiskDecision(
        approved=True, signal=signal, quantity=qty,
        capital_at_risk=qty * stop_dist,
        trade_value=qty * signal.entry_price,
    )


def main() -> None:
    print("\n── Day 6: Execution Layer Tests ──\n")

    # Fresh capital state for each run
    _open_trades.clear()
    state = reset_state()

    # ── Test 1: Open a paper trade ─────────────────────────────────────────
    print("Test 1 — Open paper trade (RELIANCE)")
    sig  = make_signal()
    dec  = make_decision(sig)
    trade = open_trade(dec)

    assert trade.is_open, "trade should be OPEN"
    assert trade.symbol == "RELIANCE"
    assert trade.quantity == 14
    assert trade.mode == "paper"
    print(f"  ✓ Trade opened in memory: {trade}")

    # Verify in Supabase
    time.sleep(0.5)
    rows = db_fetch("trades", {"id": trade.id})
    assert rows, f"trade {trade.id} not found in Supabase"
    assert rows[0]["status"] == "OPEN"
    print(f"  ✓ Supabase trades row: status={rows[0]['status']}  qty={rows[0]['quantity']}")

    pos_rows = db_fetch("positions", {"trade_id": trade.id})
    assert pos_rows, "position row not found in Supabase"
    print(f"  ✓ Supabase positions row: symbol={pos_rows[0]['symbol']}  avg_price={pos_rows[0]['avg_price']}")

    # Capital check
    expected_cash = state.total_capital - trade.cost_basis
    assert abs(state.available_cash - expected_cash) < 1, (
        f"cash mismatch: {state.available_cash:.0f} vs {expected_cash:.0f}"
    )
    print(f"  ✓ Capital reserved: cash=₹{state.available_cash:,.0f}  positions={state.open_positions_count}")

    # ── Test 2: Close at stop-loss ─────────────────────────────────────────
    print("\nTest 2 — Close at stop-loss")
    sl_price = trade.stop_loss   # ₹1,293.44
    closed = close_trade(trade, exit_price=sl_price, reason="stop_loss")

    assert closed.status == "CLOSED"
    expected_pnl = (sl_price - trade.entry_price) * trade.quantity
    assert abs(closed.pnl - expected_pnl) < 0.01, f"PnL mismatch: {closed.pnl} vs {expected_pnl:.2f}"
    print(f"  ✓ Trade closed at SL: exit=₹{sl_price:.2f}  PnL=₹{closed.pnl:+,.2f}")

    time.sleep(0.5)
    rows = db_fetch("trades", {"id": trade.id})
    assert rows[0]["status"] == "CLOSED"
    assert rows[0]["exit_price"] is not None
    print(f"  ✓ Supabase updated: status=CLOSED  exit_price={rows[0]['exit_price']}")

    # Capital released
    assert state.open_positions_count == 0
    print(f"  ✓ Capital released: cash=₹{state.available_cash:,.0f}  daily_PnL=₹{state.daily_realised_pnl:+,.2f}")

    # ── Test 3: Close at target (profit) ───────────────────────────────────
    print("\nTest 3 — Open second trade (TCS), close at target")
    state2 = reset_state()
    _open_trades.clear()

    sig2  = make_signal("TCS", entry=2272.80)
    dec2  = make_decision(sig2, qty=4)
    trade2 = open_trade(dec2)

    tgt_price = trade2.target
    closed2 = close_trade(trade2, exit_price=tgt_price, reason="target_hit")

    expected_profit = (tgt_price - trade2.entry_price) * trade2.quantity
    assert closed2.pnl > 0, "expected a profit on target hit"
    assert abs(closed2.pnl - expected_profit) < 0.01
    print(f"  ✓ Target hit: exit=₹{tgt_price:.2f}  PnL=₹{closed2.pnl:+,.2f}")
    print(f"  ✓ Daily PnL: ₹{state2.daily_realised_pnl:+,.2f}")

    # ── Test 4: Audit log entries ──────────────────────────────────────────
    print("\nTest 4 — Audit log entries")
    time.sleep(0.5)
    audit_rows = db_fetch("audit_log", {"trade_id": trade2.id})
    events = [r["event"] for r in audit_rows]
    assert "trade_opened" in events, f"missing trade_opened in audit: {events}"
    assert "trade_closed" in events, f"missing trade_closed in audit: {events}"
    print(f"  ✓ Audit events recorded: {events}")

    # ── Test 5: Load open trades from DB on restart ────────────────────────
    print("\nTest 5 — Reload open trades from Supabase on restart")
    _open_trades.clear()
    state3 = reset_state()

    sig3  = make_signal("HDFCBANK", entry=749.60)
    dec3  = make_decision(sig3, qty=10)
    trade3 = open_trade(dec3)

    # Simulate restart: clear memory, reload from DB
    _open_trades.clear()
    load_open_trades_from_db()

    assert trade3.id in _open_trades, "trade3 should be reloaded from DB"
    reloaded = _open_trades[trade3.id]
    assert reloaded.symbol == "HDFCBANK"
    print(f"  ✓ Reloaded from DB: {reloaded}")

    # Cleanup — close it
    close_trade(reloaded, exit_price=760.0, reason="test_cleanup")

    print("\n── Capital summary ──")
    print(f"  {state3.summary()}")

    print("\n── All 5 execution tests passed ──")
    logger.success("Day 6 test passed — Execution layer working correctly.")


if __name__ == "__main__":
    main()
