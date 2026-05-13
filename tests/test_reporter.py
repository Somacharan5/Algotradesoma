"""
Day 8 success-criteria test:
  Sends real messages to your Telegram chat. Check your phone after running.

  1. Generic alert
  2. Trade-opened alert (paper RELIANCE BUY)
  3. Trade-closed alert — stop loss
  4. Trade-closed alert — target hit
  5. Status message (capital + positions)
  6. Circuit-breaker alert
  7. Daily summary message

Run with:  python -m tests.test_reporter
Requires:  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env
"""
from __future__ import annotations

from datetime import datetime

from execution.executor import TradeRecord
from reporting.reporter import (
    send_alert, send_trade_opened, send_trade_closed,
    send_status, send_circuit_alert, send_daily_summary,
)
from risk.capital import reset_state
from loguru import logger
import time


def make_trade(
    symbol: str = "RELIANCE",
    entry: float = 1358.80,
    qty: int = 14,
    status: str = "OPEN",
    exit_price: float | None = None,
    pnl: float | None = None,
) -> TradeRecord:
    atr = 32.68
    t = TradeRecord(
        id="test-trade-001",
        symbol=symbol, exchange="NSE",
        direction="BUY", quantity=qty,
        entry_price=entry,
        stop_loss=round(entry - 2 * atr, 2),
        target=round(entry + 3 * atr, 2),
        mode="paper",
        status=status,
        exit_price=exit_price,
        pnl=pnl,
    )
    return t


def check(ok: bool, label: str) -> None:
    assert ok, f"[FAIL] {label}: Telegram send returned False (check token/chat_id)"
    print(f"  ✓ {label}: sent")


def main() -> None:
    print("\n── Day 8: Reporter Tests (check your Telegram!) ──\n")

    state = reset_state()

    # ── 1. Generic alert ───────────────────────────────────────────────────
    print("Test 1 — Generic alert")
    ok = send_alert("🧪 Day 8 reporter test — agent is alive!", emoji="")
    check(ok, "generic alert")
    time.sleep(0.5)

    # ── 2. Trade opened ────────────────────────────────────────────────────
    print("Test 2 — Trade opened (RELIANCE BUY)")
    trade = make_trade()
    ok = send_trade_opened(trade)
    check(ok, "trade opened")
    time.sleep(0.5)

    # ── 3. Trade closed — stop loss ────────────────────────────────────────
    print("Test 3 — Trade closed (stop loss)")
    sl_trade = make_trade(
        status="CLOSED",
        exit_price=trade.stop_loss,
        pnl=round((trade.stop_loss - trade.entry_price) * trade.quantity, 2),
    )
    ok = send_trade_closed(sl_trade, reason="stop_loss")
    check(ok, "trade closed (SL)")
    time.sleep(0.5)

    # ── 4. Trade closed — target ───────────────────────────────────────────
    print("Test 4 — Trade closed (target hit)")
    tgt_trade = make_trade(
        symbol="TCS", entry=2272.80, qty=4,
        status="CLOSED",
        exit_price=2370.84,
        pnl=round((2370.84 - 2272.80) * 4, 2),
    )
    ok = send_trade_closed(tgt_trade, reason="target_hit")
    check(ok, "trade closed (target)")
    time.sleep(0.5)

    # ── 5. Status ──────────────────────────────────────────────────────────
    print("Test 5 — Status message")
    state.daily_realised_pnl = tgt_trade.pnl
    ok = send_status(state)
    check(ok, "status")
    time.sleep(0.5)

    # ── 6. Circuit breaker ─────────────────────────────────────────────────
    print("Test 6 — Circuit breaker alert")
    ok = send_circuit_alert(loss_pct=0.036)
    check(ok, "circuit alert")
    time.sleep(0.5)

    # ── 7. Daily summary ───────────────────────────────────────────────────
    print("Test 7 — Daily summary")
    summary = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "realised_pnl": 1_450.50,
        "unrealised_pnl": -210.0,
        "total_trades": 3,
        "winners": 2,
        "losers": 1,
        "win_rate": 66.7,
        "capital_end": 101_450.50,
        "circuit_tripped": False,
        "open_positions": 1,
    }
    ok = send_daily_summary(summary)
    check(ok, "daily summary")

    print("\n── All 7 reporter tests passed — check your Telegram! ──")
    logger.success("Day 8 test passed — Reporter working correctly.")


if __name__ == "__main__":
    main()
