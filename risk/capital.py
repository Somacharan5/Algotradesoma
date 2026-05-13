from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from loguru import logger

from config import settings


@dataclass
class CapitalState:
    """
    Tracks available capital and daily PnL for paper (and live) trading.
    Loaded from Supabase at startup; falls back to config defaults.
    """
    total_capital: float = field(default_factory=lambda: settings.PAPER_CAPITAL)
    available_cash: float = field(default_factory=lambda: settings.PAPER_CAPITAL)
    daily_realised_pnl: float = 0.0
    daily_unrealised_pnl: float = 0.0
    open_positions_count: int = 0
    trade_date: date = field(default_factory=date.today)
    circuit_tripped: bool = False

    # ── Derived ────────────────────────────────────────────────────────────

    @property
    def daily_pnl(self) -> float:
        return self.daily_realised_pnl + self.daily_unrealised_pnl

    @property
    def daily_loss_pct(self) -> float:
        """Fraction of total capital lost today (positive means loss)."""
        return -self.daily_pnl / self.total_capital if self.total_capital else 0.0

    @property
    def is_circuit_tripped(self) -> bool:
        return self.circuit_tripped or self.daily_loss_pct >= settings.DAILY_CIRCUIT_BREAKER

    # ── Mutators ───────────────────────────────────────────────────────────

    def reserve(self, amount: float) -> None:
        """Lock cash for a pending order."""
        self.available_cash -= amount
        self.open_positions_count += 1
        logger.debug(f"Capital reserved ₹{amount:,.0f} | cash left ₹{self.available_cash:,.0f}")

    def release(self, amount: float, pnl: float) -> None:
        """Release cash when a position closes."""
        self.available_cash += amount
        self.daily_realised_pnl += pnl
        self.open_positions_count = max(0, self.open_positions_count - 1)
        logger.debug(
            f"Capital released ₹{amount:,.0f} | PnL ₹{pnl:+,.0f} | "
            f"daily PnL ₹{self.daily_pnl:+,.0f}"
        )

    def update_unrealised(self, unrealised: float) -> None:
        self.daily_unrealised_pnl = unrealised

    def trip_circuit(self) -> None:
        self.circuit_tripped = True
        logger.warning(
            f"CIRCUIT BREAKER TRIPPED — daily loss {self.daily_loss_pct:.2%} "
            f"≥ limit {settings.DAILY_CIRCUIT_BREAKER:.2%}. No new trades today."
        )

    def reset_for_new_day(self) -> None:
        self.daily_realised_pnl = 0.0
        self.daily_unrealised_pnl = 0.0
        self.circuit_tripped = False
        self.trade_date = date.today()
        logger.info(f"Capital state reset for new trading day ({self.trade_date}).")

    def summary(self) -> str:
        return (
            f"Capital: ₹{self.total_capital:,.0f} | "
            f"Cash: ₹{self.available_cash:,.0f} | "
            f"Daily PnL: ₹{self.daily_pnl:+,.0f} ({self.daily_pnl/self.total_capital:+.2%}) | "
            f"Positions: {self.open_positions_count}/{settings.MAX_OPEN_POSITIONS} | "
            f"Circuit: {'TRIPPED' if self.is_circuit_tripped else 'OK'}"
        )


# Module-level singleton — shared across all layers in one process
_state: CapitalState | None = None


def get_state() -> CapitalState:
    global _state
    if _state is None:
        _state = CapitalState()
        logger.info(f"Capital state initialised: {_state.summary()}")
    return _state


def reset_state() -> CapitalState:
    """Force-create a fresh state (used in tests)."""
    global _state
    _state = CapitalState()
    return _state
