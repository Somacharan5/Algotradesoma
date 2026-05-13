from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from loguru import logger

from broker.angel import AngelBroker
from broker.market_data import get_token
from config import settings
from db.client import db_audit, db_upsert, db_fetch
from execution.executor import get_open_trades, close_trade, TradeRecord
from risk.capital import CapitalState, get_state
from risk.officer import check_circuit_breaker


# ── Mark-to-market ─────────────────────────────────────────────────────────

def mark_to_market(broker: AngelBroker, state: Optional[CapitalState] = None) -> dict[str, float]:
    """
    Fetch current LTP for every open position.
    Updates unrealised PnL on the CapitalState and each position row in Supabase.
    Returns {symbol: ltp}.
    """
    if state is None:
        state = get_state()

    trades = get_open_trades()
    if not trades:
        logger.debug("Mark-to-market: no open positions.")
        return {}

    prices: dict[str, float] = {}
    total_unrealised = 0.0

    for trade in trades:
        try:
            token = get_token(trade.symbol, trade.exchange)
            ltp = broker.get_ltp(trade.exchange, trade.symbol, token)
            unrealised = trade.unrealised_pnl(ltp)
            total_unrealised += unrealised
            prices[trade.symbol] = ltp

            db_upsert("positions", {
                "trade_id": trade.id,
                "symbol": trade.symbol,
                "exchange": trade.exchange,
                "direction": trade.direction,
                "quantity": trade.quantity,
                "avg_price": trade.entry_price,
                "current_price": ltp,
                "stop_loss": trade.stop_loss,
                "target": trade.target,
                "unrealised_pnl": round(unrealised, 2),
                "mode": trade.mode,
            }, on_conflict="symbol")

            logger.debug(
                f"  MTM {trade.symbol}: ltp=₹{ltp:.2f}  "
                f"unrealised=₹{unrealised:+.2f}"
            )
        except Exception as exc:
            logger.warning(f"MTM failed for {trade.symbol}: {exc}")

    state.update_unrealised(total_unrealised)
    logger.info(
        f"Mark-to-market complete: {len(prices)} position(s)  "
        f"total unrealised=₹{total_unrealised:+,.2f}"
    )
    return prices


# ── Daily PnL rollup ───────────────────────────────────────────────────────

def rollup_daily_pnl(state: Optional[CapitalState] = None) -> dict:
    """
    Write (or update) today's row in the daily_pnl table.
    Called after every trade close and at end of day.
    """
    if state is None:
        state = get_state()

    today = date.today().isoformat()
    row = {
        "trade_date": today,
        "realised_pnl": round(state.daily_realised_pnl, 2),
        "unrealised_pnl": round(state.daily_unrealised_pnl, 2),
        "num_trades": state.open_positions_count,
        "capital_start": round(state.total_capital, 2),
        "capital_end": round(state.total_capital + state.daily_realised_pnl, 2),
        "circuit_tripped": state.circuit_tripped,
    }
    db_upsert("daily_pnl", row, on_conflict="trade_date")
    logger.debug(
        f"Daily PnL rolled up: realised=₹{state.daily_realised_pnl:+,.2f}  "
        f"unrealised=₹{state.daily_unrealised_pnl:+,.2f}"
    )
    return row


# ── Circuit breaker enforcement ────────────────────────────────────────────

def enforce_circuit_breaker(state: Optional[CapitalState] = None) -> bool:
    """
    Check daily loss. If ≥ 3.5%, trip the circuit and close ALL open positions
    at last known prices (emergency close in paper mode).
    Returns True if circuit was tripped this call.
    """
    if state is None:
        state = get_state()

    if state.circuit_tripped:
        return False   # already tripped earlier

    if not check_circuit_breaker(state):
        return False   # threshold not breached

    logger.critical(
        f"CIRCUIT BREAKER FIRED — daily loss {state.daily_loss_pct:.2%} "
        f"≥ limit {settings.DAILY_CIRCUIT_BREAKER:.2%}"
    )
    db_audit("auditor", "circuit_breaker_tripped", {
        "daily_loss_pct": round(state.daily_loss_pct * 100, 3),
        "limit_pct": settings.DAILY_CIRCUIT_BREAKER * 100,
        "realised_pnl": state.daily_realised_pnl,
        "unrealised_pnl": state.daily_unrealised_pnl,
    })

    # Emergency-close all open positions at entry price (paper)
    for trade in get_open_trades():
        logger.warning(f"  Emergency close: {trade.symbol} (circuit breaker)")
        close_trade(trade, exit_price=trade.entry_price, reason="circuit_breaker")

    rollup_daily_pnl(state)
    return True


# ── Audit wrappers ─────────────────────────────────────────────────────────

def log_signal(symbol: str, direction: str, strategy: str, confidence: float) -> None:
    db_audit("strategy", "signal_generated", {
        "symbol": symbol,
        "direction": direction,
        "strategy": strategy,
        "confidence": confidence,
    })


def log_risk_decision(symbol: str, approved: bool, qty: int, reason: str = "") -> None:
    db_audit("risk", "risk_evaluated", {
        "symbol": symbol,
        "approved": approved,
        "quantity": qty,
        "reason": reason,
    })


def log_compliance_decision(symbol: str, passed: bool, reason: str = "") -> None:
    db_audit("compliance", "compliance_checked", {
        "symbol": symbol,
        "passed": passed,
        "reason": reason,
    })


# ── End-of-day summary ─────────────────────────────────────────────────────

def end_of_day_summary(state: Optional[CapitalState] = None) -> dict:
    """
    Build EOD summary dict used by Day 8 Reporter for Telegram message.
    Also writes final daily_pnl row.
    """
    if state is None:
        state = get_state()

    today = date.today().isoformat()
    trades_today = db_fetch("trades", {"status": "CLOSED"})
    trades_today = [t for t in trades_today if (t.get("closed_at") or "").startswith(today)]

    winners = [t for t in trades_today if (t.get("pnl") or 0) > 0]
    losers  = [t for t in trades_today if (t.get("pnl") or 0) < 0]

    summary = {
        "date": today,
        "realised_pnl": round(state.daily_realised_pnl, 2),
        "unrealised_pnl": round(state.daily_unrealised_pnl, 2),
        "total_trades": len(trades_today),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": round(len(winners) / len(trades_today) * 100, 1) if trades_today else 0.0,
        "capital_end": round(state.total_capital + state.daily_realised_pnl, 2),
        "circuit_tripped": state.circuit_tripped,
        "open_positions": len(get_open_trades()),
    }

    rollup_daily_pnl(state)
    db_audit("auditor", "end_of_day_summary", summary)
    logger.info(f"EOD summary: {summary}")
    return summary
