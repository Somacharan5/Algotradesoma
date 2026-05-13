"""
Day 1 success-criteria test:
  Connect to Angel One and print holdings + funds.
  Run with:  python -m tests.test_broker
  Requires:  .env with real Angel credentials
"""
from __future__ import annotations

import json
from broker.angel import AngelBroker
from loguru import logger


def main() -> None:
    broker = AngelBroker()

    logger.info("Connecting to Angel One…")
    broker.connect()

    logger.info("── Profile ──")
    profile = broker.get_profile()
    print(json.dumps(profile, indent=2, default=str))

    logger.info("── Holdings (long-term portfolio) ──")
    holdings = broker.get_holdings()
    if holdings:
        for h in holdings:
            print(f"  {h.get('tradingsymbol'):20s}  qty={h.get('quantity')}  ltp={h.get('ltp')}")
    else:
        print("  (no holdings)")

    logger.info("── Open Positions (intraday / swing) ──")
    positions = broker.get_positions()
    if positions:
        for p in positions:
            print(f"  {p.get('tradingsymbol'):20s}  qty={p.get('netqty')}  pnl={p.get('realised')}")
    else:
        print("  (no open positions)")

    logger.info("── Funds ──")
    funds = broker.get_funds()
    print(json.dumps(funds, indent=2, default=str))

    logger.success("Day 1 test passed — broker connection working.")


if __name__ == "__main__":
    main()
