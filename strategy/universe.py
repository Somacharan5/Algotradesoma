from __future__ import annotations

# Liquid Nifty 50 stocks — Angel One NSE symbols
# Chosen for tight spreads, high volume, reliable candle data
NIFTY50 = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK",
    "LT", "HCLTECH", "AXISBANK", "ASIANPAINT", "MARUTI",
    "SUNPHARMA", "TITAN", "BAJFINANCE", "NESTLEIND", "WIPRO",
    "ULTRACEMCO", "POWERGRID", "NTPC", "ONGC", "COALINDIA",
    "TECHM", "INDUSINDBK", "BAJAJFINSV", "ADANIPORTS", "GRASIM",
    "M&M", "TMPV", "TATASTEEL", "JSWSTEEL", "HINDALCO",
    "ADANIENT", "SBILIFE", "HDFCLIFE", "EICHERMOT", "DRREDDY",
    "CIPLA", "APOLLOHOSP", "HEROMOTOCO", "BRITANNIA", "TATACONSUM",
    "BAJAJ-AUTO", "BPCL", "SHRIRAMFIN", "TRENT", "BEL",
]

# Nifty Next 50 — the next tier of large, liquid names
NIFTY_NEXT50 = [
    "LICI", "IOC", "GAIL", "VEDL", "PNB",
    "BANKBARODA", "DLF", "SIEMENS", "ABB", "ADANIGREEN",
    "ADANIPOWER", "AMBUJACEM", "BAJAJHLDNG", "BOSCHLTD", "CANBK",
    "CHOLAFIN", "DABUR", "GODREJCP", "HAVELLS", "HAL",
    "ICICIPRULI", "ICICIGI", "INDIGO", "JINDALSTEL", "TMCV",
    "LODHA", "MARICO", "MOTHERSON", "NAUKRI", "PIDILITIND",
    "PFC", "RECLTD", "SHREECEM", "SRF", "TATAPOWER",
    "TORNTPHARM", "TVSMOTOR", "UNITDSPR", "VBL", "ZYDUSLIFE",
    "IRFC", "JSWENERGY", "NHPC", "BERGEPAINT", "COLPAL",
    "SBICARD", "MUTHOOTFIN", "POLYCAB", "DIXON", "MAXHEALTH",
]

# Nifty 100 ≈ Nifty 50 + Next 50. Symbols that fail to resolve in the
# Angel One scrip master are skipped with a warning during the scan.
NIFTY100 = NIFTY50 + NIFTY_NEXT50

# Active scan universe
WATCHLIST = NIFTY100
