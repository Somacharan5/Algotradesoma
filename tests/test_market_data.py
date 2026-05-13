"""
Day 2 success-criteria test:
  1. Download scrip master and look up RELIANCE token
  2. Fetch 3 months of daily candles
  3. Run indicators (EMA20, EMA50, ATR14, RSI14, volume)
  4. Print last 5 rows + summary stats

Run with:  python -m tests.test_market_data
"""
from __future__ import annotations

from broker.angel import AngelBroker
from broker.market_data import (
    Interval,
    download_scrip_master,
    get_token,
    get_candles,
    search_instruments,
)
from strategy.indicators import add_indicators, is_uptrend, is_high_volume
from loguru import logger


def main() -> None:
    # ── 1. Scrip master ───────────────────────────────────────────────────
    logger.info("Step 1 — Downloading/verifying scrip master…")
    download_scrip_master()

    token = get_token("RELIANCE", "NSE")
    logger.info(f"RELIANCE token: {token}")

    # Bonus: show search results
    results = search_instruments("RELIANCE", "NSE")
    logger.info(f"Search 'RELIANCE' → {len(results)} instruments found")
    for r in results[:5]:
        print(f"  {r['name']:30s}  token={r['token']:8s}  type={r.get('instrumenttype','')}")

    # ── 2. Connect broker + fetch candles ─────────────────────────────────
    logger.info("\nStep 2 — Connecting to Angel One + fetching candles…")
    broker = AngelBroker()
    broker.connect()

    df = get_candles(
        broker,
        symbol="RELIANCE",
        exchange="NSE",
        interval=Interval.ONE_DAY,
        days=90,
    )

    if df.empty:
        logger.error("No candle data returned. Exiting.")
        return

    print(f"\nTotal candles fetched: {len(df)}")
    print(df[["timestamp", "open", "high", "low", "close", "volume"]].tail(5).to_string(index=False))

    # ── 3. Indicators ─────────────────────────────────────────────────────
    logger.info("\nStep 3 — Computing indicators…")
    df = add_indicators(df)

    last = df.iloc[-1]
    print(f"\n── Latest bar ({last['timestamp'].date()}) ──")
    print(f"  Close      : ₹{last['close']:.2f}")
    print(f"  EMA 20     : ₹{last['ema_fast']:.2f}")
    print(f"  EMA 50     : ₹{last['ema_slow']:.2f}")
    print(f"  ATR 14     : ₹{last['atr']:.2f}  ({last['atr']/last['close']*100:.2f}% of price)")
    print(f"  RSI 14     : {last['rsi']:.1f}")
    print(f"  Vol ratio  : {last['vol_ratio']:.2f}x 20-day avg")
    print(f"  Uptrend    : {is_uptrend(df)}")
    print(f"  High volume: {is_high_volume(df)}")

    print(f"\n── Last 5 bars with indicators ──")
    cols = ["timestamp", "close", "ema_fast", "ema_slow", "atr", "rsi", "vol_ratio"]
    print(df[cols].tail(5).round(2).to_string(index=False))

    logger.success("Day 2 test passed — market data pipeline working.")


if __name__ == "__main__":
    main()
