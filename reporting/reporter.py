from __future__ import annotations

import httpx
from loguru import logger

from config import settings
from execution.executor import TradeRecord
from risk.capital import CapitalState, get_state

# ── Low-level send ─────────────────────────────────────────────────────────

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _send(text: str, parse_mode: str = "HTML") -> bool:
    """
    POST a message to the configured Telegram chat.
    Returns True on success, False on failure (never raises).
    """
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — message skipped.")
        return False

    url = TELEGRAM_API.format(token=settings.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
    }
    try:
        resp = httpx.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error(f"Telegram send failed: {exc}")
        return False


# ── Trade alerts ───────────────────────────────────────────────────────────

def send_trade_opened(trade: TradeRecord) -> bool:
    pct_sl  = abs(trade.entry_price - trade.stop_loss) / trade.entry_price * 100
    pct_tgt = abs(trade.target - trade.entry_price) / trade.entry_price * 100
    rr = round(pct_tgt / pct_sl, 2) if pct_sl else 0

    emoji = "🟢" if trade.direction == "BUY" else "🔴"
    mode_tag = "📄 PAPER" if trade.mode == "paper" else "💰 LIVE"

    text = (
        f"{emoji} <b>Trade Opened</b> — {mode_tag}\n"
        f"\n"
        f"<b>Symbol</b>  : {trade.symbol}\n"
        f"<b>Direction</b>: {trade.direction}\n"
        f"<b>Qty</b>      : {trade.quantity} shares\n"
        f"<b>Entry</b>    : ₹{trade.entry_price:.2f}\n"
        f"<b>Stop Loss</b>: ₹{trade.stop_loss:.2f}  (-{pct_sl:.2f}%)\n"
        f"<b>Target</b>   : ₹{trade.target:.2f}  (+{pct_tgt:.2f}%)\n"
        f"<b>R:R</b>      : 1 : {rr}\n"
        f"<b>Value</b>    : ₹{trade.cost_basis:,.0f}"
    )
    logger.info(f"Telegram → trade opened: {trade.symbol}")
    return _send(text)


def send_trade_closed(trade: TradeRecord, reason: str = "") -> bool:
    pnl = trade.pnl or 0.0
    emoji = "✅" if pnl >= 0 else "❌"
    reason_tag = {
        "stop_loss": "🛑 Stop Loss Hit",
        "target_hit": "🎯 Target Hit",
        "circuit_breaker": "⚡ Circuit Breaker",
        "manual": "🤚 Manual Close",
    }.get(reason, reason or "Closed")

    text = (
        f"{emoji} <b>Trade Closed</b> — {reason_tag}\n"
        f"\n"
        f"<b>Symbol</b>    : {trade.symbol}\n"
        f"<b>Entry</b>     : ₹{trade.entry_price:.2f}\n"
        f"<b>Exit</b>      : ₹{trade.exit_price:.2f}\n"
        f"<b>Qty</b>       : {trade.quantity} shares\n"
        f"<b>P&L</b>       : ₹{pnl:+,.2f}\n"
        f"<b>Mode</b>      : {'📄 Paper' if trade.mode == 'paper' else '💰 Live'}"
    )
    logger.info(f"Telegram → trade closed: {trade.symbol}  PnL=₹{pnl:+,.2f}")
    return _send(text)


# ── Status / summary ───────────────────────────────────────────────────────

def send_status(state: CapitalState | None = None) -> bool:
    if state is None:
        state = get_state()

    from execution.executor import get_open_trades
    trades = get_open_trades()
    circuit = "⚡ TRIPPED" if state.is_circuit_tripped else "✅ OK"

    lines = [
        "📊 <b>Agent Status</b>",
        "",
        f"<b>Capital</b>     : ₹{state.total_capital:,.0f}",
        f"<b>Available</b>   : ₹{state.available_cash:,.0f}",
        f"<b>Daily P&L</b>   : ₹{state.daily_pnl:+,.2f} ({state.daily_pnl/state.total_capital:+.2%})",
        f"<b>Positions</b>   : {state.open_positions_count}/{settings.MAX_OPEN_POSITIONS}",
        f"<b>Circuit</b>     : {circuit}",
    ]

    if trades:
        lines += ["", "<b>Open Positions:</b>"]
        for t in trades:
            lines.append(f"  • {t.symbol}  {t.quantity}×  entry=₹{t.entry_price:.2f}")

    return _send("\n".join(lines))


def send_daily_summary(summary: dict) -> bool:
    pnl = summary.get("realised_pnl", 0)
    unreal = summary.get("unrealised_pnl", 0)
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    circuit = "⚡ YES" if summary.get("circuit_tripped") else "✅ No"

    text = (
        f"📅 <b>Daily Summary — {summary.get('date', '')}</b>\n"
        f"\n"
        f"<b>Realised P&L</b>  : {pnl_emoji} ₹{pnl:+,.2f}\n"
        f"<b>Unrealised</b>   : ₹{unreal:+,.2f}\n"
        f"<b>Trades</b>       : {summary.get('total_trades', 0)} "
        f"({summary.get('winners', 0)}W / {summary.get('losers', 0)}L)\n"
        f"<b>Win Rate</b>     : {summary.get('win_rate', 0):.1f}%\n"
        f"<b>Capital End</b>  : ₹{summary.get('capital_end', 0):,.0f}\n"
        f"<b>Open Pos</b>     : {summary.get('open_positions', 0)}\n"
        f"<b>Circuit</b>      : {circuit}"
    )
    logger.info(f"Telegram → daily summary sent")
    return _send(text)


def send_alert(message: str, emoji: str = "⚠️") -> bool:
    return _send(f"{emoji} {message}")


def send_circuit_alert(loss_pct: float) -> bool:
    return _send(
        f"⚡ <b>CIRCUIT BREAKER TRIPPED</b>\n"
        f"\n"
        f"Daily loss reached <b>{loss_pct:.2%}</b> — breached 3.5% limit.\n"
        f"All open positions have been closed.\n"
        f"No new trades will be placed today."
    )
