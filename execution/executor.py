from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from broker.angel import AngelBroker
from compliance import checker as compliance
from config import settings
from db.client import db_insert, db_update, db_fetch, db_upsert, db_audit
from risk.capital import get_state
from risk.officer import RiskDecision
from strategy.engine import Signal


# ── Trade record ───────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    id: str
    symbol: str
    exchange: str
    direction: str
    quantity: int
    entry_price: float
    stop_loss: float
    target: float
    mode: str
    status: str = "OPEN"
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    broker_order_id: Optional[str] = None
    signal_id: Optional[str] = None
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: Optional[datetime] = None

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.entry_price

    @property
    def is_open(self) -> bool:
        return self.status == "OPEN"

    def unrealised_pnl(self, ltp: float) -> float:
        if self.direction == "BUY":
            return (ltp - self.entry_price) * self.quantity
        return (self.entry_price - ltp) * self.quantity

    def __str__(self) -> str:
        return (
            f"[{self.status}] {self.direction} {self.quantity}×{self.symbol} "
            f"@ ₹{self.entry_price:.2f}  sl=₹{self.stop_loss:.2f}  tgt=₹{self.target:.2f}"
        )


# ── In-memory open trades registry ────────────────────────────────────────

_open_trades: dict[str, TradeRecord] = {}   # trade_id → TradeRecord


def get_open_trades() -> list[TradeRecord]:
    return list(_open_trades.values())


# ── Open a trade ───────────────────────────────────────────────────────────

def open_trade(
    decision: RiskDecision,
    broker: Optional[AngelBroker] = None,
) -> TradeRecord:
    """
    Execute a risk-approved signal.

    Paper mode: simulates fill at entry_price.
    Live mode:  places a market order via Angel One (requires broker).
    """
    sig = decision.signal
    mode = settings.TRADING_MODE
    trade_id = str(uuid.uuid4())
    broker_order_id: Optional[str] = None

    if mode == "live":
        if broker is None:
            raise RuntimeError("broker required for live trading")
        token = _get_token(sig.symbol)
        broker_order_id = broker.place_order(
            symbol=sig.symbol,
            token=token,
            exchange=sig.exchange,
            direction=sig.direction,
            quantity=decision.quantity,
            order_type="MARKET",
            product="DELIVERY",
        )
        logger.info(f"Live order placed: {broker_order_id}")
    else:
        logger.info(
            f"Paper trade opened: {sig.direction} {decision.quantity}×{sig.symbol} "
            f"@ ₹{sig.entry_price:.2f}"
        )

    trade = TradeRecord(
        id=trade_id,
        symbol=sig.symbol,
        exchange=sig.exchange,
        direction=sig.direction,
        quantity=decision.quantity,
        entry_price=sig.entry_price,
        stop_loss=sig.stop_loss,
        target=sig.target,
        mode=mode,
        broker_order_id=broker_order_id,
    )

    # ── Update state ───────────────────────────────────────────────────────
    capital = get_state()
    capital.reserve(trade.cost_basis)
    compliance.register_open(sig.symbol)
    _open_trades[trade_id] = trade

    # ── Persist to Supabase ────────────────────────────────────────────────
    row = {
        "id": trade_id,
        "mode": mode,
        "symbol": sig.symbol,
        "exchange": sig.exchange,
        "direction": sig.direction,
        "quantity": decision.quantity,
        "entry_price": sig.entry_price,
        "stop_loss": sig.stop_loss,
        "target": sig.target,
        "status": "OPEN",
        "broker_order_id": broker_order_id,
    }
    db_insert("trades", row)

    db_upsert("positions", {
        "trade_id": trade_id,
        "symbol": sig.symbol,
        "exchange": sig.exchange,
        "direction": sig.direction,
        "quantity": decision.quantity,
        "avg_price": sig.entry_price,
        "stop_loss": sig.stop_loss,
        "target": sig.target,
        "mode": mode,
    }, on_conflict="symbol")

    db_audit("execution", "trade_opened", {
        "symbol": sig.symbol,
        "direction": sig.direction,
        "quantity": decision.quantity,
        "entry_price": sig.entry_price,
        "stop_loss": sig.stop_loss,
        "target": sig.target,
        "mode": mode,
    }, trade_id=trade_id)

    logger.info(f"Trade recorded: {trade}")
    return trade


# ── Close a trade ──────────────────────────────────────────────────────────

def close_trade(
    trade: TradeRecord,
    exit_price: float,
    reason: str = "manual",
    broker: Optional[AngelBroker] = None,
) -> TradeRecord:
    """
    Close an open trade at exit_price.
    Calculates PnL, releases capital, updates Supabase.
    """
    if not trade.is_open:
        raise ValueError(f"Trade {trade.id} is already closed.")

    mode = settings.TRADING_MODE

    if mode == "live" and broker:
        token = _get_token(trade.symbol)
        exit_direction = "SELL" if trade.direction == "BUY" else "BUY"
        broker.place_order(
            symbol=trade.symbol,
            token=token,
            exchange=trade.exchange,
            direction=exit_direction,
            quantity=trade.quantity,
            order_type="MARKET",
            product="DELIVERY",
        )

    pnl = (exit_price - trade.entry_price) * trade.quantity
    if trade.direction == "SELL":
        pnl = -pnl

    trade.exit_price = exit_price
    trade.pnl = round(pnl, 2)
    trade.status = "CLOSED"
    trade.closed_at = datetime.now(timezone.utc)

    # ── Update state ───────────────────────────────────────────────────────
    capital = get_state()
    capital.release(trade.cost_basis, pnl)
    compliance.deregister_open(trade.symbol)
    _open_trades.pop(trade.id, None)

    # ── Persist to Supabase ────────────────────────────────────────────────
    db_update("trades", {"id": trade.id}, {
        "exit_price": exit_price,
        "pnl": trade.pnl,
        "status": "CLOSED",
        "closed_at": trade.closed_at.isoformat(),
    })

    db_update("positions", {"trade_id": trade.id}, {
        "current_price": exit_price,
        "unrealised_pnl": 0,
    })

    db_audit("execution", "trade_closed", {
        "symbol": trade.symbol,
        "exit_price": exit_price,
        "pnl": trade.pnl,
        "reason": reason,
        "mode": mode,
    }, trade_id=trade.id)

    pnl_sign = "+" if pnl >= 0 else ""
    logger.info(
        f"Trade closed: {trade.symbol}  exit=₹{exit_price:.2f}  "
        f"PnL={pnl_sign}₹{pnl:,.2f}  reason={reason}"
    )
    return trade


# ── Stop / target monitor ──────────────────────────────────────────────────

def check_stops(broker: AngelBroker) -> list[TradeRecord]:
    """
    Fetch LTP for every open position and close any that have hit SL or target.
    Returns list of trades that were closed.
    """
    closed: list[TradeRecord] = []
    for trade in get_open_trades():
        try:
            from broker.market_data import get_token
            token = get_token(trade.symbol, trade.exchange)
            ltp = broker.get_ltp(trade.exchange, trade.symbol, token)
        except Exception as exc:
            logger.warning(f"Could not fetch LTP for {trade.symbol}: {exc}")
            continue

        unrealised = trade.unrealised_pnl(ltp)
        logger.debug(
            f"  {trade.symbol}  ltp=₹{ltp:.2f}  "
            f"sl=₹{trade.stop_loss:.2f}  tgt=₹{trade.target:.2f}  "
            f"PnL=₹{unrealised:+.0f}"
        )

        if trade.direction == "BUY":
            if ltp <= trade.stop_loss:
                close_trade(trade, ltp, reason="stop_loss")
                closed.append(trade)
            elif ltp >= trade.target:
                close_trade(trade, ltp, reason="target_hit")
                closed.append(trade)
        else:
            if ltp >= trade.stop_loss:
                close_trade(trade, ltp, reason="stop_loss")
                closed.append(trade)
            elif ltp <= trade.target:
                close_trade(trade, ltp, reason="target_hit")
                closed.append(trade)

    return closed


# ── Helpers ────────────────────────────────────────────────────────────────

def _get_token(symbol: str, exchange: str = "NSE") -> str:
    from broker.market_data import get_token
    return get_token(symbol, exchange)


def load_open_trades_from_db() -> None:
    """On startup, reload any OPEN trades from Supabase into memory."""
    rows = db_fetch("trades", {"status": "OPEN"})
    for row in rows:
        trade = TradeRecord(
            id=row["id"],
            symbol=row["symbol"],
            exchange=row["exchange"],
            direction=row["direction"],
            quantity=row["quantity"],
            entry_price=float(row["entry_price"]),
            stop_loss=float(row["stop_loss"]),
            target=float(row["target"]),
            mode=row["mode"],
            status=row["status"],
            broker_order_id=row.get("broker_order_id"),
        )
        _open_trades[trade.id] = trade
        compliance.register_open(trade.symbol)
    if rows:
        logger.info(f"Loaded {len(rows)} open trade(s) from Supabase.")
