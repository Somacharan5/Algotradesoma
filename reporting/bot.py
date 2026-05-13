from __future__ import annotations

"""
Telegram command bot — runs as a background task inside the main asyncio loop.

Commands (only accepted from TELEGRAM_CHAT_ID):
  /start    — greeting
  /status   — capital + open positions
  /positions — list open trades with unrealised PnL
  /pause    — stop opening new trades
  /resume   — re-enable new trades
  /summary  — trigger EOD summary now
  /help     — command list
"""

import asyncio
from loguru import logger
from telegram import Update
from telegram.ext import (
    Application, ApplicationBuilder,
    CommandHandler, ContextTypes,
)

from config import settings
from reporting.reporter import send_status, send_daily_summary, _send
from risk.capital import get_state
from audit.auditor import end_of_day_summary
from execution.executor import get_open_trades

# ── Pause flag (read by Day 9 orchestrator before opening trades) ──────────
_paused: bool = False


def is_paused() -> bool:
    return _paused


def _authorised(update: Update) -> bool:
    """Only respond to the configured chat."""
    return str(update.effective_chat.id) == str(settings.TELEGRAM_CHAT_ID)


# ── Handlers ───────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorised(update):
        return
    await update.message.reply_text(
        "🤖 <b>Trading Agent Online</b>\n\n"
        "Commands:\n"
        "/status — capital &amp; positions\n"
        "/positions — open trades\n"
        "/pause — stop new trades\n"
        "/resume — enable new trades\n"
        "/summary — EOD summary now\n"
        "/help — this message",
        parse_mode="HTML",
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorised(update):
        return
    pause_line = "\n\n⏸ <b>Trading is PAUSED</b>" if _paused else ""
    send_status(get_state())
    if pause_line:
        _send(pause_line)


async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorised(update):
        return
    trades = get_open_trades()
    if not trades:
        await update.message.reply_text("No open positions.")
        return

    lines = ["📋 <b>Open Positions</b>\n"]
    for t in trades:
        lines.append(
            f"• <b>{t.symbol}</b>  {t.direction}  {t.quantity} shares\n"
            f"  Entry: ₹{t.entry_price:.2f}  SL: ₹{t.stop_loss:.2f}  Tgt: ₹{t.target:.2f}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    global _paused
    if not _authorised(update):
        return
    _paused = True
    logger.warning("Trading PAUSED via Telegram command.")
    await update.message.reply_text(
        "⏸ <b>Trading paused.</b>\n"
        "Existing positions continue to be monitored.\n"
        "No new trades will be opened.\n"
        "Send /resume to re-enable.",
        parse_mode="HTML",
    )


async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    global _paused
    if not _authorised(update):
        return
    _paused = False
    logger.info("Trading RESUMED via Telegram command.")
    await update.message.reply_text(
        "▶️ <b>Trading resumed.</b>\n"
        "Agent will scan for new signals at next market check.",
        parse_mode="HTML",
    )


async def cmd_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorised(update):
        return
    summary = end_of_day_summary(get_state())
    send_daily_summary(summary)


# ── Bot lifecycle ──────────────────────────────────────────────────────────

def build_app() -> Application:
    app = (
        ApplicationBuilder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .build()
    )
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("status",    cmd_status))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("pause",     cmd_pause))
    app.add_handler(CommandHandler("resume",    cmd_resume))
    app.add_handler(CommandHandler("summary",   cmd_summary))
    return app


async def run_bot() -> None:
    """
    Start long-polling in the background.
    Called by the Day 9 orchestrator as an asyncio task.
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set — bot disabled.")
        return

    app = build_app()
    logger.info("Telegram bot starting (long-poll)…")
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot polling active.")
        # Keep alive — orchestrator will cancel this task on shutdown
        await asyncio.Event().wait()
