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
    generateSession handles token storage internally — do NOT call setAccessToken after.
    Returns an authenticated SmartConnect session.
    """
    obj = SmartConnect(api_key=settings.ANGEL_API_KEY)
    totp_code = _generate_totp()

    # generateSession internally sets access_token, refresh_token, feed_token
    # and calls getProfile. It returns the profile dict on success.
    result = obj.generateSession(
        clientCode=settings.ANGEL_CLIENT_ID,
        password=settings.ANGEL_MPIN,
        totp=totp_code,
    )

    if not result or result.get("status") is False:
        message = result.get("message", "Unknown error") if result else "No response"
        raise RuntimeError(f"Angel SmartAPI login failed: {message}")

    logger.info(f"Angel One login successful — client: {settings.ANGEL_CLIENT_ID}")
    return obj


def refresh_session(obj: SmartConnect) -> SmartConnect:
    """Refresh an existing session; re-login if refresh fails."""
    try:
        obj.generateToken(obj.refresh_token)
        logger.info("Angel One session refreshed.")
    except Exception as exc:
        logger.warning(f"Session refresh failed, re-logging in: {exc}")
        obj = login()
    return obj
