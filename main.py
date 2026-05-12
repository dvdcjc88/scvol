#!/usr/bin/env python3
"""Entry point for the PH Spending Anomaly Telegram Bot."""

from __future__ import annotations

import asyncio
import logging
import sys

import structlog

from config import settings
from data.ingestion import run_ingestion_pipeline
from data.pipeline import init_db
from bot.handlers import build_application

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ]
)
logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
# Silence noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("crewai").setLevel(logging.WARNING)

log = logging.getLogger(__name__)


async def _setup():
    log.info("Initializing database...")
    await init_db()

    log.info("Running data ingestion pipeline...")
    results = await run_ingestion_pipeline(force=False)
    for source, result in results.items():
        status = result.get("status", "?")
        rows = result.get("rows", 0)
        mock = result.get("mock", False)
        log.info(f"  {source}: {status}, {rows} rows {'(mock)' if mock else '(live)'}")


async def main():
    await _setup()

    app = build_application()
    await app.initialize()
    await app.start()

    log.info(f"Bot started. Model: {settings.openrouter_model}")
    log.info(f"Mock data mode: {settings.use_mock_data}")

    await app.updater.start_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )

    log.info("Bot is running. Press Ctrl+C to stop.")
    stop_event = asyncio.Event()

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down...")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        log.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())
