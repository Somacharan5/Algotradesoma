"""
Trading Agent — main async orchestrator.
Wires all 7 layers into a single event loop.

Usage:
    python main.py
"""
from __future__ import annotations

import asyncio
import signal
import sys
from datetime import datetime, time, timedelta

import pytz
from loguru import logger

from audit.auditor import (
    enforce_circuit_breaker, end_of_day_summary,
    log_signal, log_risk_decision, log_compliance_decision,
    mark_to_market, rollup_daily_pnl,
)
from broker.angel import AngelBroker
from broker.market_data import download_scrip_master
from compliance.checker import run_all as compliance_check
from config import settings
from execution.executor import (
    open_trade, check_stops, load_open_trades_from_db, get_open_trades,
)
from reporting.bot import run_bot, is_paused
from reporting.reporter import send_alert, send_trade_opened, send_trade_closed, send_daily_summary
from risk.capital import get_state
from risk.officer import evaluate as risk_evaluate
from strategy.engine import scan, StrategyConfig
from strategy.universe import WATCHLIST

# ── Timezone + market schedule ─────────────────────────────────────────────

IST = pytz.timezone("Asia/Kolkata")

SCAN_TIME      = time(9, 20)    # morning signal scan
MONITOR_INTERVAL_MIN = 15       # position check every 15 min
EOD_TIME       = time(15, 35)   # end-of-day summary + rollup
REFRESH_TIME   = time(8, 55)    # session refresh before market open

# ── Globals ────────────────────────────────────────────────────────────────

_broker: AngelBroker | None = None
_shutdown_event = asyncio.Event()


def _now_ist() -> datetime:
    return datetime.now(IST)


def _seconds_until(target: time) -> float:
    """Seconds from now until next occurrence of target time (IST)."""
    now = _now_ist()
    target_dt = now.replace(
        hour=target.hour, minute=target.minute, second=0, microsecond=0
    )
    if target_dt <= now:
        target_dt += timedelta(days=1)
    return (target_dt - now).total_seconds()


def _is_market_day() -> bool:
    from compliance.holidays import is_holiday
    now = _now_ist()
    return now.weekday() < 5 and not is_holiday(now.date())


# ── Startup / shutdown ─────────────────────────────────────────────────────

async def _startup() -> AngelBroker:
    logger.info("=" * 50)
    logger.info("Trading Agent starting up")
    logger.info(f"Mode     : {settings.TRADING_MODE.upper()}")
    logger.info(f"Capital  : ₹{settings.PAPER_CAPITAL:,.0f}")
    logger.info(f"Universe : {len(WATCHLIST)} symbols")
    logger.info("=" * 50)

    send_alert(
        f"<b>Trading Agent Online</b>\n"
        f"Mode: {settings.TRADING_MODE.upper()}  |  Capital: ₹{settings.PAPER_CAPITAL:,.0f}\n"
        f"Watching {len(WATCHLIST)} symbols.",
        emoji="🤖",
    )

    # Scrip master (cached, background)
    await asyncio.to_thread(download_scrip_master)

    # Broker login
    broker = AngelBroker()
    await asyncio.to_thread(broker.connect)
    logger.info("Broker connected.")

    # Reload any open trades from DB (handles process restarts)
    await asyncio.to_thread(load_open_trades_from_db)
    state = get_state()
    logger.info(f"Capital state: {state.summary()}")

    return broker


async def _shutdown(_broker: AngelBroker) -> None:
    logger.info("Shutting down…")
    send_alert("Trading Agent shutting down.", emoji="🔌")
    _shutdown_event.set()


# ── Core trading cycle ─────────────────────────────────────────────────────

async def _run_signal_scan(broker: AngelBroker) -> None:
    """Morning scan: strategy → compliance → risk → execution."""
    logger.info("── Morning signal scan ──")
    state = get_state()

    if is_paused():
        logger.info("Trading is paused — skipping scan.")
        send_alert("Scan skipped — trading is paused. Send /resume to enable.", emoji="⏸")
        return

    if state.is_circuit_tripped:
        logger.warning("Circuit breaker active — skipping scan.")
        return

    cfg = StrategyConfig(candle_days=90)
    signals = await asyncio.to_thread(scan, broker, WATCHLIST, "NSE", cfg)

    if not signals:
        logger.info("No signals today.")
        return

    for sig in signals:
        if _shutdown_event.is_set():
            break

        log_signal(sig.symbol, sig.direction, sig.strategy, sig.confidence)

        # Compliance
        avg_vol = sig.metadata.get("avg_volume", 5_000_000)
        comp = compliance_check(sig, avg_volume=avg_vol)
        log_compliance_decision(sig.symbol, comp.passed, comp.rejection_reason)
        if not comp.passed:
            logger.info(f"Compliance blocked {sig.symbol}: {comp.rejection_reason}")
            continue

        # Risk
        decision = await asyncio.to_thread(risk_evaluate, sig, state)
        log_risk_decision(sig.symbol, decision.approved, decision.quantity, decision.rejection_reason)
        if not decision.approved:
            logger.info(f"Risk blocked {sig.symbol}: {decision.rejection_reason}")
            continue

        # Execute
        trade = await asyncio.to_thread(open_trade, decision, broker if settings.TRADING_MODE == "live" else None)
        send_trade_opened(trade)
        await asyncio.to_thread(rollup_daily_pnl, state)

        # Stop after max positions reached
        if state.open_positions_count >= settings.MAX_OPEN_POSITIONS:
            logger.info("Max positions reached — stopping scan.")
            break


async def _run_position_monitor(broker: AngelBroker) -> None:
    """Check LTP, MTM, stop/target hits, circuit breaker."""
    state = get_state()
    trades = get_open_trades()

    if not trades:
        logger.debug("Monitor: no open positions.")
        return

    logger.info(f"── Position monitor ({len(trades)} open) ──")

    # Mark to market
    await asyncio.to_thread(mark_to_market, broker, state)

    # Check stop/target hits
    closed = await asyncio.to_thread(check_stops, broker)
    for trade in closed:
        reason = "stop_loss" if trade.exit_price <= trade.stop_loss else "target_hit"
        send_trade_closed(trade, reason=reason)
        await asyncio.to_thread(rollup_daily_pnl, state)

    # Circuit breaker
    tripped = await asyncio.to_thread(enforce_circuit_breaker, state)
    if tripped:
        from reporting.reporter import send_circuit_alert
        send_circuit_alert(state.daily_loss_pct)

    logger.info(f"Monitor done — {state.summary()}")


async def _run_eod(_broker: AngelBroker) -> None:
    """End-of-day: summary, rollup, Telegram message."""
    logger.info("── End-of-day routine ──")
    state = get_state()
    summary = await asyncio.to_thread(end_of_day_summary, state)
    send_daily_summary(summary)
    state.reset_for_new_day()
    logger.info("EOD complete — state reset for tomorrow.")


# ── Event loops ────────────────────────────────────────────────────────────

async def _scanner_loop(broker: AngelBroker) -> None:
    """Waits until 09:20 IST on market days, then runs the scan."""
    while not _shutdown_event.is_set():
        secs = _seconds_until(SCAN_TIME)
        logger.info(f"Next scan in {secs/3600:.1f}h ({SCAN_TIME.strftime('%H:%M')} IST)")
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=secs)
            break  # shutdown requested
        except asyncio.TimeoutError:
            pass

        if _is_market_day():
            try:
                await _run_signal_scan(broker)
            except Exception as exc:
                logger.error(f"Scanner error: {exc}")
                send_alert(f"Scanner error: {exc}", emoji="❌")
        else:
            logger.info("Non-trading day — scan skipped.")


async def _monitor_loop(broker: AngelBroker) -> None:
    """Runs position monitor every MONITOR_INTERVAL_MIN minutes."""
    interval = MONITOR_INTERVAL_MIN * 60
    while not _shutdown_event.is_set():
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass

        if _is_market_day():
            now = _now_ist().time()
            market_open  = time(9, 15)
            market_close = time(15, 30)
            if market_open <= now <= market_close:
                try:
                    await _run_position_monitor(broker)
                except Exception as exc:
                    logger.error(f"Monitor error: {exc}")
                    send_alert(f"Monitor error: {exc}", emoji="❌")


async def _eod_loop(broker: AngelBroker) -> None:
    """Waits until 15:35 IST then runs EOD routine."""
    while not _shutdown_event.is_set():
        secs = _seconds_until(EOD_TIME)
        logger.info(f"Next EOD in {secs/3600:.1f}h ({EOD_TIME.strftime('%H:%M')} IST)")
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=secs)
            break
        except asyncio.TimeoutError:
            pass

        if _is_market_day():
            try:
                await _run_eod(broker)
            except Exception as exc:
                logger.error(f"EOD error: {exc}")


async def _refresh_loop(broker: AngelBroker) -> None:
    """Refreshes Angel One session token before market open each day."""
    while not _shutdown_event.is_set():
        secs = _seconds_until(REFRESH_TIME)
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=secs)
            break
        except asyncio.TimeoutError:
            pass
        try:
            await asyncio.to_thread(broker.refresh)
            logger.info("Broker session refreshed.")
        except Exception as exc:
            logger.warning(f"Session refresh failed: {exc}")


# ── Main entry ─────────────────────────────────────────────────────────────

def _handle_signal(sig: int, _frame: object) -> None:
    logger.info(f"Signal {sig} received — initiating shutdown.")
    _shutdown_event.set()


async def run() -> None:
    broker = await _startup()

    # Register OS signal handlers for clean shutdown
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _handle_signal)

    tasks = [
        asyncio.create_task(_scanner_loop(broker),  name="scanner"),
        asyncio.create_task(_monitor_loop(broker),  name="monitor"),
        asyncio.create_task(_eod_loop(broker),      name="eod"),
        asyncio.create_task(_refresh_loop(broker),  name="refresh"),
        asyncio.create_task(run_bot(),              name="telegram_bot"),
    ]

    logger.info(f"All {len(tasks)} tasks running. Ctrl+C to stop.")

    try:
        await asyncio.gather(*tasks)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await _shutdown(broker)


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    logger.add(
        "logs/agent_{time:YYYY-MM-DD}.log",
        rotation="1 day", retention="30 days",
        level="DEBUG", enqueue=True,
    )
    asyncio.run(run())
