"""
Reminders in the support group when a user waits too long for a reply.

A wait starts with the user's first message after the last reply that reached
them (a manager's message, a draft sent with its button, an automatic reply of
a category) and ends with the next such reply. Policy auto-replies are not
replies. Every threshold in ``reminders.after_minutes`` posts one reminder
into the user's topic per wait. Off unless ``reminders.enabled`` is set.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from asyncpg import Pool

from app.bot.policy import PolicyEngine
from app.bot.policy.schema import RemindersSection
from app.bot.utils.redis import RedisStorage
from app.bot.utils.texts import TextMessage
from app.config import Config

logger = logging.getLogger(__name__)


def reminders_enabled(policy_engine: PolicyEngine | None) -> bool:
    """True when the policy turns the reply reminders on."""
    return policy_engine is not None and policy_engine.reminders.enabled


async def start_reply_wait(redis: RedisStorage, user_id: int) -> None:
    """The user's message reached the topic. Errors are only logged."""
    try:
        await redis.start_reply_wait(user_id)
    except Exception as ex:  # noqa: BLE001 - reminders never block the message
        logger.warning("Failed to start the reply wait of user %s: %s", user_id, ex)


async def end_reply_wait(redis: RedisStorage, user_id: int) -> None:
    """A reply reached the user. Errors are only logged."""
    try:
        await redis.end_reply_wait(user_id)
    except Exception as ex:  # noqa: BLE001 - reminders never block the reply
        logger.warning("Failed to end the reply wait of user %s: %s", user_id, ex)


def waited_hours(minutes: int) -> int:
    """Whole hours for the reminder text, rounded half up, at least 1."""
    return max(1, (minutes + 30) // 60)


async def check_reply_waits(
    bot: Bot, config: Config, redis: RedisStorage, section: RemindersSection
) -> None:
    """Post the reminders that are due. A failed send is logged and not retried."""
    waits = await redis.get_due_reply_waits(section.after_minutes, section.skip_categories)
    for wait in waits:
        # Claimed before sending, so a second running instance cannot repeat it.
        if not await redis.claim_reply_reminder(wait["user_id"], wait["since"], wait["level"]):
            continue
        txt = TextMessage(wait["language_code"] or "ru")
        try:
            await bot.send_message(
                chat_id=config.bot.GROUP_ID,
                message_thread_id=wait["message_thread_id"],
                text=txt.get("reply_reminder").format(hours=waited_hours(wait["waited_minutes"])),
            )
        except Exception as ex:  # noqa: BLE001
            logger.warning("Failed to post the reply reminder for user %s: %s", wait["user_id"], ex)


async def run_reply_reminders(
    bot: Bot, config: Config, pool: Pool, section: RemindersSection
) -> None:
    """
    Check the waits every ``check_interval_minutes`` until cancelled. The waits
    live in PostgreSQL, so a restart only delays the reminders due meanwhile.
    """
    redis = RedisStorage(pool)
    while True:
        try:
            await check_reply_waits(bot, config, redis, section)
        except Exception as ex:  # noqa: BLE001 - keep checking after a database hiccup
            logger.warning("Reply reminder check failed: %s", ex)
        await asyncio.sleep(section.check_interval_minutes * 60)
