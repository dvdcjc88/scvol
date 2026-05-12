from __future__ import annotations

import logging

from bot.handlers import build_application

logger = logging.getLogger(__name__)


async def start_bot() -> None:
    app = build_application()
    await app.initialize()
    await app.start()
    logger.info("Telegram bot started, polling for updates...")
    await app.updater.start_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )


async def stop_bot(app) -> None:
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
