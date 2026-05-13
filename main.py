"""Entry point — orchestrates all agent layers. Placeholder for Day 2+."""
from __future__ import annotations

import asyncio
from loguru import logger


async def run() -> None:
    logger.info("Trading agent starting…")
    # TODO: initialise layers and start event loop


if __name__ == "__main__":
    asyncio.run(run())
