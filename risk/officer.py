from __future__ import annotations

import math
from dataclasses import dataclass
from loguru import logger

from config import settings
from risk.capital import CapitalState, get_state
from strategy.engine import Signal


# ── Decision ───────────────────────────────────────────────────────────────

@dataclass
class RiskDecision:
    approved: bool
    signal: Signal
    quantity: int = 0
    capital_at_risk: float = 0.0      # qty × stop_distance
    trade_value: float = 0.0          # qty × entry_price
    rejection_reason: str = ""

    def __str__(self) -> str:
        if self.approved:
            return (
                f"APPROVED  {self.signal.symbol}  qty={self.quantity}  "
                f"value=₹{self.trade_value:,.0f}  risk=₹{self.capital_at_risk:,.0f}"
            )
        return f"REJECTED  {self.signal.symbol}  reason='{self.rejection_reason}'"


# ── Rules ──────────────────────────────────────────────────────────────────

# Maximum fraction of total capital to deploy in a single trade
MAX_SINGLE_TRADE_PCT = 0.20   # never more than 20% in one name
MIN_TRADE_VALUE = 5_000       # skip if position < ₹5,000 (rounding kills edge)


def _reject(signal: Signal, reason: str) -> RiskDecision:
    logger.warning(f"Risk REJECT {signal.symbol}: {reason}")
    return RiskDecision(approved=False, signal=signal, rejection_reason=reason)


def evaluate(signal: Signal, state: CapitalState | None = None) -> RiskDecision:
    """
    Run all risk checks on a Signal and return a RiskDecision.

    Checks (in order):
      1. Circuit breaker — halt if daily loss ≥ 3.5%
      2. Max open positions — halt if already at limit
      3. Stop distance sanity — reject if SL == entry
      4. Position sizing — 1% capital risk per trade
      5. Minimum trade value — reject tiny positions
      6. Available cash — reject if not enough liquidity
    """
    if state is None:
        state = get_state()

    # ── 1. Circuit breaker ─────────────────────────────────────────────────
    if state.is_circuit_tripped:
        return _reject(
            signal,
            f"circuit breaker active — daily loss {state.daily_loss_pct:.2%} "
            f"≥ limit {settings.DAILY_CIRCUIT_BREAKER:.2%}",
        )

    # ── 2. Max open positions ──────────────────────────────────────────────
    if state.open_positions_count >= settings.MAX_OPEN_POSITIONS:
        return _reject(
            signal,
            f"max open positions reached ({settings.MAX_OPEN_POSITIONS})",
        )

    # ── 3. Stop distance sanity ────────────────────────────────────────────
    stop_distance = abs(signal.entry_price - signal.stop_loss)
    if stop_distance <= 0:
        return _reject(signal, "stop distance is zero — malformed signal")

    # ── 4. Position sizing: 1% risk per trade ──────────────────────────────
    risk_amount = state.total_capital * settings.RISK_PER_TRADE   # e.g. ₹1,000
    raw_qty = risk_amount / stop_distance
    quantity = max(1, math.floor(raw_qty))

    capital_at_risk = quantity * stop_distance
    trade_value = quantity * signal.entry_price

    # ── 5. Minimum trade value ─────────────────────────────────────────────
    if trade_value < MIN_TRADE_VALUE:
        return _reject(
            signal,
            f"trade value ₹{trade_value:,.0f} below minimum ₹{MIN_TRADE_VALUE:,}",
        )

    # ── 6. Single-name concentration cap ──────────────────────────────────
    max_allowed = state.total_capital * MAX_SINGLE_TRADE_PCT
    if trade_value > max_allowed:
        quantity = max(1, math.floor(max_allowed / signal.entry_price))
        trade_value = quantity * signal.entry_price
        capital_at_risk = quantity * stop_distance
        logger.debug(
            f"  {signal.symbol}: qty capped at {quantity} "
            f"(20% concentration limit = ₹{max_allowed:,.0f})"
        )

    # ── 7. Available cash ──────────────────────────────────────────────────
    if trade_value > state.available_cash:
        return _reject(
            signal,
            f"insufficient cash: need ₹{trade_value:,.0f}, "
            f"available ₹{state.available_cash:,.0f}",
        )

    logger.info(
        f"Risk APPROVED {signal.symbol}  qty={quantity}  "
        f"value=₹{trade_value:,.0f}  risk=₹{capital_at_risk:,.0f}  "
        f"({capital_at_risk/state.total_capital:.2%} of capital)"
    )
    return RiskDecision(
        approved=True,
        signal=signal,
        quantity=quantity,
        capital_at_risk=capital_at_risk,
        trade_value=trade_value,
    )


def check_circuit_breaker(state: CapitalState | None = None) -> bool:
    """Convenience: trip circuit if threshold breached. Returns True if tripped."""
    if state is None:
        state = get_state()
    if state.is_circuit_tripped and not state.circuit_tripped:
        state.trip_circuit()
    return state.is_circuit_tripped
