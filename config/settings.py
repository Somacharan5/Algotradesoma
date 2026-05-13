from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Always resolve .env relative to the repo root (parent of this file's package)
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(f"Required env var '{key}' is not set. Check your .env file.")
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ── Angel One ──────────────────────────────────────────────────────────────
ANGEL_API_KEY: str = _require("ANGEL_API_KEY")
ANGEL_CLIENT_ID: str = _require("ANGEL_CLIENT_ID")
ANGEL_MPIN: str = _require("ANGEL_MPIN")
ANGEL_TOTP_SECRET: str = _require("ANGEL_TOTP_SECRET")

# ── Telegram ───────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = _optional("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID: str = _optional("TELEGRAM_CHAT_ID")

# ── Supabase ───────────────────────────────────────────────────────────────
SUPABASE_URL: str = _optional("SUPABASE_URL")
SUPABASE_SERVICE_KEY: str = _optional("SUPABASE_SERVICE_KEY")

# ── Trading ────────────────────────────────────────────────────────────────
TRADING_MODE: str = _optional("TRADING_MODE", "paper")       # "paper" | "live"
PAPER_CAPITAL: float = float(_optional("PAPER_CAPITAL", "100000"))
RISK_PER_TRADE: float = 0.01       # 1% of capital per trade
DAILY_CIRCUIT_BREAKER: float = 0.035  # halt if daily loss > 3.5%
MAX_OPEN_POSITIONS: int = 5
