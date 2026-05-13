from __future__ import annotations

import pyotp
from SmartApi import SmartConnect
from loguru import logger
from config import settings


def _generate_totp() -> str:
    totp = pyotp.TOTP(settings.ANGEL_TOTP_SECRET)
    return totp.now()


def login() -> SmartConnect:
    """
    Log in to Angel One SmartAPI using MPIN + TOTP.
    Returns an authenticated SmartConnect session.
    Raises on auth failure.
    """
    obj = SmartConnect(api_key=settings.ANGEL_API_KEY)
    totp_code = _generate_totp()

    data = obj.generateSession(
        clientCode=settings.ANGEL_CLIENT_ID,
        password=settings.ANGEL_MPIN,
        totp=totp_code,
    )

    if not data or data.get("status") is False:
        message = data.get("message", "Unknown error") if data else "No response"
        raise RuntimeError(f"Angel SmartAPI login failed: {message}")

    tokens = data.get("data", {})
    jwt_token = tokens.get("jwtToken", "")
    refresh_token = tokens.get("refreshToken", "")

    obj.setAccessToken(jwt_token)
    obj.setRefreshToken(refresh_token)

    logger.info(f"Angel One login successful — client: {settings.ANGEL_CLIENT_ID}")
    return obj


def refresh_session(obj: SmartConnect) -> SmartConnect:
    """Refresh an existing session using the stored refresh token."""
    try:
        data = obj.generateToken(obj.refresh_token)
        tokens = data.get("data", {})
        obj.setAccessToken(tokens.get("jwtToken", ""))
        obj.setRefreshToken(tokens.get("refreshToken", ""))
        logger.info("Angel One session refreshed.")
    except Exception as exc:
        logger.warning(f"Session refresh failed, re-logging in: {exc}")
        obj = login()
    return obj
