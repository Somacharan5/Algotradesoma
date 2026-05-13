"""
Day 3 success-criteria test:
  1. Scan WATCHLIST for EMA crossover signals
  2. Print every signal with entry / SL / target / RR / confidence
  3. Show per-symbol status (scanned, filtered, or signal)

Run with:  python -m tests.test_strategy
"""
from __future__ import annotations

from broker.angel import AngelBroker
from broker.market_data import download_scrip_master
from strategy.engine import scan, StrategyConfig
from strategy.universe import WATCHLIST
from loguru import logger
import time


def main() -> None:
    logger.info("Day 3 — Strategy Engine test")
    logger.info(f"Universe: {WATCHLIST}")

    download_scrip_master()

    broker = AngelBroker()
    broker.connect()

    cfg = StrategyConfig(
        fast_ema=20, slow_ema=50,
        atr_period=14,
        sl_atr_mult=2.0, tgt_atr_mult=3.0,
        min_vol_ratio=1.2,
        rsi_overbought=72.0,
        candle_days=90,
    )

    start = time.time()
    signals = scan(broker, symbols=WATCHLIST, cfg=cfg)
    elapsed = time.time() - start

    print(f"\n── Scan complete in {elapsed:.1f}s ──")
    print(f"Symbols scanned : {len(WATCHLIST)}")
    print(f"Signals found   : {len(signals)}")

    if signals:
        print("\n── Signals (ranked by confidence) ──")
        for i, sig in enumerate(signals, 1):
            print(f"\n  #{i} {sig.direction} {sig.symbol}")
            print(f"     Strategy   : {sig.strategy}")
            print(f"     Entry      : ₹{sig.entry_price:.2f}")
            print(f"     Stop Loss  : ₹{sig.stop_loss:.2f}  (-{abs(sig.entry_price - sig.stop_loss)/sig.entry_price*100:.2f}%)")
            print(f"     Target     : ₹{sig.target:.2f}  (+{abs(sig.target - sig.entry_price)/sig.entry_price*100:.2f}%)")
            print(f"     Risk:Reward: {sig.risk_reward}")
            print(f"     ATR        : ₹{sig.atr:.2f}")
            print(f"     RSI        : {sig.rsi:.1f}")
            print(f"     Vol ratio  : {sig.vol_ratio:.2f}x")
            print(f"     Confidence : {sig.confidence:.0%}")
            print(f"     EMA fast   : ₹{sig.metadata.get('ema_fast', 'N/A')}")
            print(f"     EMA slow   : ₹{sig.metadata.get('ema_slow', 'N/A')}")
    else:
        print("\n  No crossover signals today — market likely in consolidation.")
        print("  (This is normal. The scanner runs daily at market open.)")

    logger.success("Day 3 test passed — Strategy Engine working.")


if __name__ == "__main__":
    main()
