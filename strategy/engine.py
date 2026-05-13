from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import pandas as pd
from loguru import logger

from broker.angel import AngelBroker
from broker.market_data import get_candles, Interval
from strategy.indicators import add_indicators
from strategy.universe import WATCHLIST


# ── Signal ─────────────────────────────────────────────────────────────────

@dataclass
class Signal:
    symbol: str
    exchange: str
    direction: str          # "BUY" | "EXIT"
    strategy: str
    price_at_signal: float  # close of the bar that triggered
    entry_price: float      # suggested entry (current close; execution uses market open)
    stop_loss: float        # entry - 2×ATR (BUY) or entry + 2×ATR (SHORT — not used yet)
    target: float           # entry + 3×ATR
    atr: float
    rsi: float
    vol_ratio: float
    confidence: float       # 0.0–1.0 composite score
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict = field(default_factory=dict)

    @property
    def risk_reward(self) -> float:
        risk   = abs(self.entry_price - self.stop_loss)
        reward = abs(self.target - self.entry_price)
        return round(reward / risk, 2) if risk else 0.0

    def __str__(self) -> str:
        return (
            f"[{self.direction}] {self.symbol}  "
            f"entry=₹{self.entry_price:.2f}  sl=₹{self.stop_loss:.2f}  "
            f"tgt=₹{self.target:.2f}  RR={self.risk_reward}  "
            f"RSI={self.rsi:.1f}  vol={self.vol_ratio:.2f}x  "
            f"conf={self.confidence:.0%}"
        )


# ── Strategy parameters ────────────────────────────────────────────────────

@dataclass
class StrategyConfig:
    fast_ema: int   = 20
    slow_ema: int   = 50
    atr_period: int = 14
    sl_atr_mult: float = 2.0   # stop = entry ± sl_atr_mult × ATR
    tgt_atr_mult: float = 3.0  # target = entry ± tgt_atr_mult × ATR
    min_vol_ratio: float = 1.2  # bar volume must be ≥ 1.2× 20-day avg
    rsi_overbought: float = 72.0
    rsi_oversold: float   = 28.0
    min_atr_pct: float = 0.5   # ignore stocks where ATR < 0.5% of price (illiquid)
    candle_days: int = 90


DEFAULT_CONFIG = StrategyConfig()


# ── Core logic ─────────────────────────────────────────────────────────────

def _ema_crossed_above(df: pd.DataFrame) -> bool:
    """EMA fast crossed above EMA slow on the latest bar (prev bar was below)."""
    if len(df) < 2:
        return False
    prev, curr = df.iloc[-2], df.iloc[-1]
    return prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]


def _ema_crossed_below(df: pd.DataFrame) -> bool:
    """EMA fast crossed below EMA slow on the latest bar."""
    if len(df) < 2:
        return False
    prev, curr = df.iloc[-2], df.iloc[-1]
    return prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]


def _confidence(last: pd.Series, direction: str, cfg: StrategyConfig) -> float:
    """
    Composite confidence score (0–1).
    Factors: RSI position, volume, ATR % of price, EMA gap.
    """
    score = 0.5  # base for a crossover signal

    vol_score = min(last["vol_ratio"] / 2.0, 0.25)          # max 0.25 at 2× volume
    score += vol_score

    if direction == "BUY":
        rsi_score = max(0, (60 - last["rsi"]) / 60) * 0.15  # lower RSI = more room to run
    else:
        rsi_score = max(0, (last["rsi"] - 40) / 60) * 0.15
    score += rsi_score

    atr_pct = last["atr"] / last["close"]
    atr_score = min(atr_pct / 0.03, 0.10)                   # max 0.10 at ≥3% ATR
    score += atr_score

    return round(min(score, 1.0), 2)


def evaluate_symbol(
    broker: AngelBroker,
    symbol: str,
    exchange: str = "NSE",
    cfg: StrategyConfig = DEFAULT_CONFIG,
) -> Optional[Signal]:
    """
    Run the EMA-crossover swing strategy on one symbol.
    Returns a Signal if an entry or exit is triggered, else None.
    """
    try:
        df = get_candles(broker, symbol, exchange, Interval.ONE_DAY, days=cfg.candle_days)
    except Exception as exc:
        logger.warning(f"  {symbol}: candle fetch failed — {exc}")
        return None

    if len(df) < cfg.slow_ema + 5:
        logger.debug(f"  {symbol}: not enough bars ({len(df)}), skipping.")
        return None

    df = add_indicators(df, fast=cfg.fast_ema, slow=cfg.slow_ema, atr_period=cfg.atr_period)
    last = df.iloc[-1]

    # ── Quality filters ────────────────────────────────────────────────────
    atr_pct = last["atr"] / last["close"] * 100
    if atr_pct < cfg.min_atr_pct:
        logger.debug(f"  {symbol}: ATR {atr_pct:.2f}% below threshold, skipping.")
        return None

    # ── BUY signal ─────────────────────────────────────────────────────────
    if _ema_crossed_above(df):
        if last["rsi"] >= cfg.rsi_overbought:
            logger.debug(f"  {symbol}: BUY crossover but RSI overbought ({last['rsi']:.1f}), skipping.")
            return None
        if last["vol_ratio"] < cfg.min_vol_ratio:
            logger.debug(f"  {symbol}: BUY crossover but low volume ({last['vol_ratio']:.2f}x), skipping.")
            return None

        entry = float(last["close"])
        atr_val = float(last["atr"])
        return Signal(
            symbol=symbol, exchange=exchange,
            direction="BUY", strategy="ema_crossover",
            price_at_signal=entry, entry_price=entry,
            stop_loss=round(entry - cfg.sl_atr_mult * atr_val, 2),
            target=round(entry + cfg.tgt_atr_mult * atr_val, 2),
            atr=round(atr_val, 2), rsi=round(float(last["rsi"]), 2),
            vol_ratio=round(float(last["vol_ratio"]), 2),
            confidence=_confidence(last, "BUY", cfg),
            metadata={"ema_fast": round(float(last["ema_fast"]), 2),
                      "ema_slow": round(float(last["ema_slow"]), 2)},
        )

    # ── EXIT signal ────────────────────────────────────────────────────────
    if _ema_crossed_below(df):
        entry = float(last["close"])
        atr_val = float(last["atr"])
        return Signal(
            symbol=symbol, exchange=exchange,
            direction="EXIT", strategy="ema_crossover",
            price_at_signal=entry, entry_price=entry,
            stop_loss=round(entry + cfg.sl_atr_mult * atr_val, 2),
            target=round(entry - cfg.tgt_atr_mult * atr_val, 2),
            atr=round(atr_val, 2), rsi=round(float(last["rsi"]), 2),
            vol_ratio=round(float(last["vol_ratio"]), 2),
            confidence=_confidence(last, "EXIT", cfg),
            metadata={"ema_fast": round(float(last["ema_fast"]), 2),
                      "ema_slow": round(float(last["ema_slow"]), 2)},
        )

    return None


# ── Scanner ────────────────────────────────────────────────────────────────

def scan(
    broker: AngelBroker,
    symbols: list[str] = WATCHLIST,
    exchange: str = "NSE",
    cfg: StrategyConfig = DEFAULT_CONFIG,
) -> list[Signal]:
    """
    Scan all symbols and return every triggered signal.
    Sorted by confidence descending.
    """
    logger.info(f"Scanning {len(symbols)} symbols for EMA crossover signals…")
    signals: list[Signal] = []

    for symbol in symbols:
        sig = evaluate_symbol(broker, symbol, exchange, cfg)
        if sig:
            logger.info(f"  SIGNAL → {sig}")
            signals.append(sig)
        time.sleep(0.4)  # Angel One rate limit: ~3 req/sec

    signals.sort(key=lambda s: s.confidence, reverse=True)
    logger.info(f"Scan complete — {len(signals)} signal(s) found.")
    return signals
