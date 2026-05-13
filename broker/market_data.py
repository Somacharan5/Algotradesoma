from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from loguru import logger

from broker.angel import AngelBroker

# ── Constants ──────────────────────────────────────────────────────────────

SCRIP_MASTER_URL = (
    "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
)
SCRIP_MASTER_PATH = Path(__file__).resolve().parent.parent / "data" / "scrip_master.json"

# Angel One candle interval constants
class Interval:
    ONE_DAY      = "ONE_DAY"
    ONE_HOUR     = "ONE_HOUR"
    THIRTY_MIN   = "THIRTY_MINUTE"
    FIFTEEN_MIN  = "FIFTEEN_MINUTE"
    TEN_MIN      = "TEN_MINUTE"
    FIVE_MIN     = "FIVE_MINUTE"
    THREE_MIN    = "THREE_MINUTE"
    ONE_MIN      = "ONE_MINUTE"

# In-memory cache for scrip master
_scrip_cache: dict[str, dict] = {}


# ── Instrument Master ──────────────────────────────────────────────────────

def download_scrip_master(force: bool = False) -> None:
    """
    Download Angel One scrip master JSON and cache to disk.
    Re-downloads only if file is older than 1 day, or force=True.
    """
    SCRIP_MASTER_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not force and SCRIP_MASTER_PATH.exists():
        age = time.time() - SCRIP_MASTER_PATH.stat().st_mtime
        if age < 86400:  # 24 hours
            logger.debug("Scrip master fresh, skipping download.")
            return

    logger.info("Downloading Angel One scrip master…")
    resp = requests.get(SCRIP_MASTER_URL, timeout=30)
    resp.raise_for_status()
    SCRIP_MASTER_PATH.write_text(resp.text, encoding="utf-8")
    logger.info(f"Scrip master saved → {SCRIP_MASTER_PATH}")


def _load_scrip_cache() -> None:
    global _scrip_cache
    if _scrip_cache:
        return
    if not SCRIP_MASTER_PATH.exists():
        download_scrip_master()
    raw: list[dict] = json.loads(SCRIP_MASTER_PATH.read_text(encoding="utf-8"))
    # key: "EXCHANGE:SYMBOL"  e.g. "NSE:RELIANCE"
    _scrip_cache = {
        f"{r['exch_seg']}:{r['name']}": r
        for r in raw
        if r.get("instrumenttype") in ("", "EQ", None)  # equities only
    }
    logger.debug(f"Scrip cache loaded: {len(_scrip_cache):,} instruments.")


def get_instrument(symbol: str, exchange: str = "NSE") -> dict:
    """Return scrip master row for symbol. Raises KeyError if not found."""
    _load_scrip_cache()
    key = f"{exchange}:{symbol}"
    if key not in _scrip_cache:
        raise KeyError(f"Instrument not found: {key}. Check symbol/exchange.")
    return _scrip_cache[key]


def get_token(symbol: str, exchange: str = "NSE") -> str:
    """Return the Angel One symboltoken for a given symbol."""
    return get_instrument(symbol, exchange)["token"]


def search_instruments(query: str, exchange: str = "NSE") -> list[dict]:
    """Return up to 10 instruments whose name contains query (case-insensitive)."""
    _load_scrip_cache()
    q = query.upper()
    return [
        v for k, v in _scrip_cache.items()
        if exchange in k and q in k
    ][:10]


# ── Candle Data ────────────────────────────────────────────────────────────

def get_candles(
    broker: AngelBroker,
    symbol: str,
    exchange: str = "NSE",
    interval: str = Interval.ONE_DAY,
    days: int = 90,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    Fetch OHLCV candles for a symbol and return as a DataFrame.

    Columns: timestamp, open, high, low, close, volume
    Dates auto-calculated from `days` if from_date/to_date not given.
    """
    token = get_token(symbol, exchange)

    if to_date is None:
        to_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    if from_date is None:
        from_dt = datetime.now() - timedelta(days=days)
        from_date = from_dt.strftime("%Y-%m-%d %H:%M")

    logger.info(f"Fetching {interval} candles: {symbol} [{from_date} → {to_date}]")

    raw = broker.get_candles(
        token=token,
        exchange=exchange,
        interval=interval,
        from_date=from_date,
        to_date=to_date,
    )

    if not raw:
        logger.warning(f"No candle data returned for {symbol}.")
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("timestamp").reset_index(drop=True)

    logger.info(f"  → {len(df)} candles for {symbol} (latest close: {df['close'].iloc[-1]:.2f})")
    return df


def get_candles_multi(
    broker: AngelBroker,
    symbols: list[str],
    exchange: str = "NSE",
    interval: str = Interval.ONE_DAY,
    days: int = 90,
) -> dict[str, pd.DataFrame]:
    """Fetch candles for multiple symbols. Returns {symbol: DataFrame}."""
    result = {}
    for symbol in symbols:
        try:
            result[symbol] = get_candles(broker, symbol, exchange, interval, days)
            time.sleep(0.3)  # Angel One rate limit buffer
        except Exception as exc:
            logger.error(f"Failed to fetch candles for {symbol}: {exc}")
    return result
