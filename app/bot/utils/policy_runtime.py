from __future__ import annotations

import asyncio
import base64
import logging
from contextlib import suppress
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.llm import LLMProvider
from app.bot.policy import Decision, EvalContext
from app.bot.policy.context import EVENT_USER_MESSAGE
from app.bot.utils.redact import redact
from app.bot.utils.redis import RedisStorage
from app.bot.utils.redis.models import UserData
from app.bot.utils.texts import TextMessage
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
    """Apply close/escalate effects and mirror auto-replies into the topic."""
    if decision.is_noop:
        return

    txt = TextMessage(user_data.language_code or "ru")
    changed = False

    if decision.escalate:
        user_data.status = "escalated"
        changed = True
        with suppress(Exception):
            await message.bot.send_message(
                chat_id=config.bot.DEV_ID,
                text=txt.get("escalated_dev").format(full_name=user_data.full_name, id=user_data.id),
            )

    if decision.close_topic and user_data.message_thread_id is not None:
        user_data.status = "closed"
        changed = True
        with suppress(TelegramBadRequest):
            await message.bot.close_forum_topic(
                chat_id=config.bot.GROUP_ID,
                message_thread_id=user_data.message_thread_id,
            )

    # Mirror auto-replies into the topic so the manager sees what the user received,
    # and record them in the conversation history for LLM context.
    if decision.auto_replies and user_data.message_thread_id is not None:
        for reply in decision.auto_replies:
            with suppress(TelegramBadRequest):
                await message.bot.send_message(
                    chat_id=config.bot.GROUP_ID,
                    message_thread_id=user_data.message_thread_id,
                    text=txt.get("auto_reply_sent").format(text=reply),
                )
            with suppress(Exception):
                await redis.append_conversation(user_data.id, "assistant", reply)

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


def _resolve_system_prompt(ai_config) -> str:
    """Prefer base64-encoded prompt, then the file, then the built-in default."""
    if ai_config.SYSTEM_PROMPT_B64:
        try:
            decoded = base64.b64decode(ai_config.SYSTEM_PROMPT_B64).decode("utf-8").strip()
            if decoded:
                return decoded
        except Exception:  # noqa: BLE001
            logger.warning("Failed to decode AI_SYSTEM_PROMPT_B64; using file/default.")
    return _read_system_prompt(ai_config.SYSTEM_PROMPT_PATH)


async def run_ai_draft(
    provider: LLMProvider,
    config: Config,
    message: Message,
    redis: RedisStorage,
    user_data: UserData,
    max_context: int,
) -> None:
    """
    Draft a suggested reply based on the conversation so far and post it into
    the user's topic with Send/Skip buttons. Best-effort: failures are logged.
    """
    if user_data.message_thread_id is None:
        return

    history = await redis.get_conversation(user_data.id, max_context)
    if not history:
        text = message_text(message)
        if not text.strip():
            return
        history = [{"role": "user", "content": text}]

    # Reply in the user's language; if it is unclear, fall back to the language
    # the user selected in the bot (language_code).
    lang = user_data.language_code or "ru"
    lang_name = {"ru": "Russian", "en": "English"}.get(lang, lang)
    txt = TextMessage(lang)
    system_prompt = _resolve_system_prompt(config.ai)
    system_prompt += f"\n\nIf the user's language is unclear, reply in {lang_name}."
    messages = [{"role": "system", "content": system_prompt}, *history]

    try:
        draft = await asyncio.wait_for(
            provider.draft_reply(messages),
            timeout=config.ai.TIMEOUT_S,
        )
    except Exception as ex:  # noqa: BLE001 - best-effort, never block the pipeline
        # Provider errors embed dashboard URLs with key hashes and can be huge.
        logger.warning("AI draft failed: %s: %s", type(ex).__name__, redact(str(ex)))
        return

    if not draft:
        return

    await redis.set_ai_draft(user_data.id, draft)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text=txt.get("ai_draft_send"), callback_data=f"ai:send:{user_data.id}"),
            InlineKeyboardButton(text=txt.get("ai_draft_skip"), callback_data=f"ai:skip:{user_data.id}"),
        ]]
    )

    with suppress(TelegramBadRequest):
        await message.bot.send_message(
            chat_id=config.bot.GROUP_ID,
            message_thread_id=user_data.message_thread_id,
            text=f"{txt.get('ai_draft_header')}\n\n{draft}",
            reply_markup=keyboard,
            parse_mode=None,
        )
