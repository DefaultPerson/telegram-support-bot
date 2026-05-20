from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.llm import LLMProvider
from app.bot.policy import Decision, EvalContext
from app.bot.policy.context import EVENT_USER_MESSAGE
from app.bot.utils.redis import RedisStorage
from app.bot.utils.redis.models import UserData
from app.config import Config

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    "You are a support assistant. Classify the incoming message and draft a "
    "concise, polite reply in the same language as the message."
)


def message_text(message: Message) -> str:
    """Return the plain text or caption of a message (empty if neither)."""
    return message.text or message.caption or ""


def build_message_context(message: Message, user_data: UserData) -> EvalContext:
    """Build an EvalContext for an incoming user message."""
    return EvalContext(
        event_type=EVENT_USER_MESSAGE,
        text=message_text(message),
        language=user_data.language_code or "en",
    )


async def apply_auto_replies(decision: Decision, message: Message) -> None:
    """Send any policy auto-replies back to the user in their private chat."""
    for text in decision.auto_replies:
        with suppress(TelegramBadRequest):
            await message.answer(text)


async def apply_post_forward(
    decision: Decision,
    message: Message,
    redis: RedisStorage,
    user_data: UserData,
    config: Config,
) -> None:
    """Apply tag/close/escalate side effects after the message was forwarded."""
    if decision.is_noop:
        return

    changed = False

    for tag in decision.tags:
        if tag not in user_data.tags:
            user_data.tags.append(tag)
            changed = True

    if decision.escalate:
        user_data.status = "escalated"
        changed = True
        with suppress(Exception):
            await message.bot.send_message(
                chat_id=config.bot.DEV_ID,
                text=f"Escalated: {user_data.full_name} (id <code>{user_data.id}</code>)",
            )

    if decision.close_topic and user_data.message_thread_id is not None:
        user_data.status = "closed"
        changed = True
        with suppress(TelegramBadRequest):
            await message.bot.close_forum_topic(
                chat_id=config.bot.GROUP_ID,
                message_thread_id=user_data.message_thread_id,
            )

    if changed and user_data.message_thread_id is not None:
        await redis.update_user(user_data.id, user_data)


def _read_system_prompt(path: str | None) -> str:
    if not path:
        return _DEFAULT_SYSTEM_PROMPT
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
        return text or _DEFAULT_SYSTEM_PROMPT
    except OSError:
        return _DEFAULT_SYSTEM_PROMPT


async def run_ai_draft(
    provider: LLMProvider,
    config: Config,
    categories: list[str],
    message: Message,
    redis: RedisStorage,
    user_data: UserData,
) -> None:
    """
    Classify the message and post a suggested reply with Send/Skip buttons
    into the user's forum topic. Best-effort: any failure is logged and ignored.
    """
    text = message_text(message)
    if not text.strip() or user_data.message_thread_id is None:
        return

    system_prompt = _read_system_prompt(config.ai.SYSTEM_PROMPT_PATH)
    try:
        result = await asyncio.wait_for(
            provider.classify_and_draft(
                text=text,
                language=user_data.language_code or "en",
                categories=categories,
                system_prompt=system_prompt,
            ),
            timeout=config.ai.TIMEOUT_S,
        )
    except Exception as ex:  # noqa: BLE001 - best-effort, never block the pipeline
        logger.warning("AI draft failed: %s", ex)
        return

    if result.category:
        user_data.ai_category = result.category
        with suppress(Exception):
            await redis.update_user(user_data.id, user_data)

    if not result.draft:
        return

    await redis.set_ai_draft(user_data.id, result.draft)

    header = "🤖 <b>AI draft</b>"
    if result.category:
        header += f" · {result.category}"
    if result.confidence is not None:
        header += f" · {result.confidence:.0%}"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Send", callback_data=f"ai:send:{user_data.id}"),
            InlineKeyboardButton(text="🗑 Skip", callback_data=f"ai:skip:{user_data.id}"),
        ]]
    )

    with suppress(TelegramBadRequest):
        await message.bot.send_message(
            chat_id=config.bot.GROUP_ID,
            message_thread_id=user_data.message_thread_id,
            text=f"{header}\n\n{result.draft}",
            reply_markup=keyboard,
        )
