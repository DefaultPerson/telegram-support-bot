"""
Heartbeat file for the container healthcheck.

The bot uses long polling and serves no HTTP, so liveness is a file: every
``interval`` seconds a successful ``getMe`` writes the current time into it.
The healthcheck treats the bot as unhealthy once the file's mtime is too old.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from aiogram import Bot

logger = logging.getLogger(__name__)

INTERVAL_S = 60


async def beat(bot: Bot, path: str) -> None:
    """Write the current Unix time into ``path`` if the Bot API answers."""
    await bot.get_me()
    Path(path).write_text(f"{int(time.time())}\n", encoding="utf-8")


async def run_heartbeat(bot: Bot, path: str, interval: float = INTERVAL_S) -> None:
    """Beat every ``interval`` seconds until cancelled; a failed beat is only logged."""
    while True:
        try:
            await beat(bot, path)
        except Exception as ex:  # noqa: BLE001 - a stale file is the signal, keep beating
            logger.warning("Heartbeat failed: %s", ex)
        await asyncio.sleep(interval)
