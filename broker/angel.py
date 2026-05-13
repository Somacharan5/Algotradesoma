from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from loguru import logger
from SmartApi import SmartConnect
from broker.auth import login, refresh_session


@dataclass
class AngelBroker:
    """
    Thin, stateful wrapper around SmartConnect.
    Call connect() before using any trading method.
    """
    _session: SmartConnect | None = field(default=None, init=False, repr=False)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def connect(self) -> None:
        self._session = login()

    def refresh(self) -> None:
        if self._session is None:
            self.connect()
        else:
            self._session = refresh_session(self._session)

    @property
    def session(self) -> SmartConnect:
        if self._session is None:
            raise RuntimeError("AngelBroker not connected. Call connect() first.")
        return self._session

    # ── Account / Portfolio ────────────────────────────────────────────────

    def get_profile(self) -> dict:
        return self._call(self.session.getProfile, self.session.refresh_token)

    def get_refresh_token(self) -> str:
        return getattr(self.session, "refresh_token", "")

    def get_holdings(self) -> list[dict]:
        data = self._call(self.session.holding)
        return data if isinstance(data, list) else []

    def get_positions(self) -> list[dict]:
        data = self._call(self.session.position)
        return data if isinstance(data, list) else []

    def get_funds(self) -> dict:
        return self._call(self.session.rmsLimit)

    # ── Market Data ────────────────────────────────────────────────────────

    def get_ltp(self, exchange: str, symbol: str, token: str) -> float:
        data = self._call(
            self.session.ltpData,
            exchange, symbol, token,
        )
        return float(data.get("ltp", 0.0))

    def get_candles(
        self,
        token: str,
        exchange: str,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> list[list]:
        """
        interval: ONE_MINUTE | THREE_MINUTE | FIVE_MINUTE | TEN_MINUTE |
                  FIFTEEN_MINUTE | THIRTY_MINUTE | ONE_HOUR | ONE_DAY
        from_date / to_date: "YYYY-MM-DD HH:MM"
        Returns list of [timestamp, open, high, low, close, volume]
        """
        params = {
            "exchange": exchange,
            "symboltoken": token,
            "interval": interval,
            "fromdate": from_date,
            "todate": to_date,
        }
        data = self._call(self.session.getCandleData, params)
        return data if isinstance(data, list) else []

    # ── Orders ─────────────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        token: str,
        exchange: str,
        direction: str,      # "BUY" | "SELL"
        quantity: int,
        order_type: str = "MARKET",
        product: str = "DELIVERY",
        price: float = 0.0,
        trigger_price: float = 0.0,
    ) -> str:
        """Returns broker order ID."""
        params = {
            "variety": "NORMAL",
            "tradingsymbol": symbol,
            "symboltoken": token,
            "transactiontype": direction,
            "exchange": exchange,
            "ordertype": order_type,
            "producttype": product,
            "duration": "DAY",
            "price": str(price),
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(quantity),
            "triggerprice": str(trigger_price),
        }
        result = self._call(self.session.placeOrder, params)
        order_id = result if isinstance(result, str) else result.get("orderid", "")
        logger.info(f"Order placed: {direction} {quantity}x{symbol} → order_id={order_id}")
        return order_id

    def cancel_order(self, order_id: str, variety: str = "NORMAL") -> bool:
        self._call(self.session.cancelOrder, variety, order_id)
        logger.info(f"Order cancelled: {order_id}")
        return True

    def get_order_book(self) -> list[dict]:
        data = self._call(self.session.orderBook)
        return data if isinstance(data, list) else []

    def get_trade_book(self) -> list[dict]:
        data = self._call(self.session.tradeBook)
        return data if isinstance(data, list) else []

    # ── Internal ───────────────────────────────────────────────────────────

    def _call(self, method: Any, *args: Any, **kwargs: Any) -> Any:
        """
        Wraps every SDK call: logs errors, extracts .data, raises on failure.
        """
        try:
            response = method(*args, **kwargs)
        except Exception as exc:
            logger.error(f"SmartAPI call {method.__name__} raised: {exc}")
            raise

        if isinstance(response, dict):
            if response.get("status") is False:
                msg = response.get("message", "Unknown SmartAPI error")
                logger.error(f"SmartAPI {method.__name__} error: {msg}")
                raise RuntimeError(msg)
            return response.get("data", response)

        return response
