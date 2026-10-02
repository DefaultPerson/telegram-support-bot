from __future__ import annotations

import asyncio
import base64
import html
import logging
import re
from contextlib import suppress
from pathlib import Path
from typing import Optional

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.markdown import hlink

from app.bot.llm import LLMProvider
from app.bot.policy import Decision, EvalContext
from app.bot.policy.context import EVENT_USER_MESSAGE
from app.bot.policy.schema import AICategory, AISection
from app.bot.types.album import Album
from app.bot.utils.admins import notify_admins, topic_link
from app.bot.utils.redact import redact
from app.bot.utils.redis import RedisStorage
from app.bot.utils.redis.models import UserData
from app.bot.utils.reminders import end_reply_wait
from app.bot.utils.texts import TextMessage
from app.bot.utils.vision import as_data_urls, collect_attachments
from app.config import Config

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    "You are a support assistant. Classify the incoming message and draft a "
    "concise, polite reply in the same language as the message."
)

_CLASSIFY_PROMPT = (
    "Classify the user's message to a support chat into exactly one of the "
    "categories below. Answer with the category key only, nothing else.\n\n"
    "Categories (key: description):\n{categories}"
)

# Telegram's limit on a forum topic name.
_TOPIC_NAME_MAX = 128


def message_text(message: Message) -> str:
    """Return the plain text or caption of a message (empty if neither)."""
    return message.text or message.caption or ""


def build_message_context(
    message: Message, user_data: UserData, first_message: bool = False
) -> EvalContext:
    """Build an EvalContext for an incoming user message."""
    return EvalContext(
        event_type=EVENT_USER_MESSAGE,
        text=message_text(message),
        language=user_data.language_code or "en",
        first_message=first_message,
    )


async def apply_auto_replies(
    decision: Decision, message: Message, redis: RedisStorage, user_data: UserData
) -> None:
    """
    Send any policy auto-replies back to the user in their private chat.

    Once-only replies the user already received are dropped from the decision,
    so later steps (topic mirror, draft suppression) see only what was sent.
    """
    sent = []
    for reply in decision.auto_replies:
        # Claimed before sending, so two messages in a row cannot both send it.
        if reply.once and not await redis.claim_auto_reply(user_data.id, reply.template_key):
            continue
        with suppress(TelegramBadRequest):
            await message.answer(reply.text)
        sent.append(reply)
    decision.auto_replies = sent


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
    # and record them in the conversation history for LLM context. A reply that
    # leaves the draft on is a notice, not an answer: kept out of the history so
    # the draft still answers the user's message instead of following the notice.
    if decision.auto_replies and user_data.message_thread_id is not None:
        for reply in decision.auto_replies:
            with suppress(TelegramBadRequest):
                await message.bot.send_message(
                    chat_id=config.bot.GROUP_ID,
                    message_thread_id=user_data.message_thread_id,
                    text=txt.get("auto_reply_sent").format(text=reply.text),
                )
            if reply.suppress_draft:
                with suppress(Exception):
                    await redis.append_conversation(user_data.id, "assistant", reply.text)

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


def _with_images(messages: list, text: str, data_urls: list) -> list:
    """
    Attach images to the turn they belong to, in OpenAI content-part format.

    The stored transcript is text-only, so the caption of the incoming message
    is already its last user turn; that turn is upgraded in place instead of
    appending a duplicate one.
    """
    parts = []
    if text.strip():
        parts.append({"type": "text", "text": text})
    parts.extend({"type": "image_url", "image_url": {"url": url}} for url in data_urls)

    if messages and messages[-1].get("role") == "user" and messages[-1].get("content") == text:
        messages[-1] = {"role": "user", "content": parts}
    else:
        messages.append({"role": "user", "content": parts})
    return messages


async def _collect_images(config: Config, message: Message, album: Optional[Album]) -> list:
    """Download the attached images as data URLs (empty when vision is off)."""
    if not config.ai.VISION:
        return []
    attachments = collect_attachments(message, album)
    if not attachments:
        return []
    return await as_data_urls(
        message.bot,
        attachments,
        max_images=config.ai.MAX_IMAGES,
        max_bytes=config.ai.IMAGE_MAX_BYTES,
    )


def category_label(ai: AISection, key: str | None) -> str | None:
    """Icon and title of a category; a key no longer in the config shows as is."""
    if key is None:
        return None
    category = ai.category(key)
    if category is None:
        return key
    return f"{category.icon} {category.title}".strip()


def parse_category(answer: str | None, categories: list[AICategory]) -> str | None:
    """
    Map the model's answer to a configured key.

    Accepts the bare key in any case, wrapped in quotes or punctuation, or one
    key (or title) mentioned in a longer answer. Anything else, ambiguous
    answers included, falls back to "other" when that key exists, else None.
    """
    keys = {c.key.lower(): c.key for c in categories}
    fallback = keys.get("other")
    if not answer:
        return fallback

    low = answer.strip().lower()
    bare = low.strip(" \t\n`'\"*.,:;!")
    if bare in keys:
        return keys[bare]

    tokens = set(re.findall(r"[\w-]+", low))
    found = {key for lowered, key in keys.items() if lowered in tokens}
    if not found:
        found = {c.key for c in categories if c.title and c.title.lower() in low}
    return found.pop() if len(found) == 1 else fallback


async def classify_message(
    provider: LLMProvider,
    config: Config,
    ai: AISection,
    message: Message,
    data_urls: list,
) -> str | None:
    """Ask the model for the category of the user's message. Best-effort: None on failure."""
    text = message_text(message)
    if not text.strip() and not data_urls:
        return parse_category(None, ai.categories)

    listing = "\n".join(f"- {c.key}: {c.title}" for c in ai.categories)
    messages = [
        {"role": "system", "content": _CLASSIFY_PROMPT.format(categories=listing)},
        {"role": "user", "content": text},
    ]
    if data_urls:
        messages = _with_images(messages, text, data_urls)

    try:
        answer = await asyncio.wait_for(
            provider.draft_reply(messages),
            timeout=config.ai.total_timeout_s,
        )
    except Exception as ex:  # noqa: BLE001 - best-effort, never block the pipeline
        logger.warning("AI classification failed: %s: %s", type(ex).__name__, redact(str(ex)))
        return None
    return parse_category(answer, ai.categories)


async def apply_category(
    config: Config,
    message: Message,
    redis: RedisStorage,
    user_data: UserData,
    ai: AISection,
    key: str,
) -> None:
    """Store the category, put its icon on the topic and notify admins. Errors are only logged."""
    try:
        await redis.set_user_category(user_data.id, key)
    except Exception as ex:  # noqa: BLE001
        logger.warning("Failed to store the category of user %s: %s", user_data.id, ex)

    category = ai.category(key)
    if category is None or user_data.message_thread_id is None:
        return

    if category.icon:
        # The topic was created with the user's name; keep it after the icon.
        name = f"{category.icon} {user_data.full_name}"[:_TOPIC_NAME_MAX]
        try:
            await message.bot.edit_forum_topic(
                chat_id=config.bot.GROUP_ID,
                message_thread_id=user_data.message_thread_id,
                name=name,
            )
        except Exception as ex:  # noqa: BLE001
            logger.warning("Failed to put the category icon on topic %s: %s",
                           user_data.message_thread_id, ex)

    if category.notify_admins:
        txt = TextMessage(user_data.language_code or "ru")
        link = topic_link(config.bot.GROUP_ID, user_data.message_thread_id)
        await notify_admins(
            message.bot,
            config,
            txt.get("category_admin_notice").format(
                category=html.escape(category_label(ai, key)),
                name=hlink(user_data.full_name, link),
                link=link,
            ),
        )


async def run_ai_layer(
    provider: LLMProvider,
    config: Config,
    message: Message,
    redis: RedisStorage,
    user_data: UserData,
    ai: AISection,
    album: Optional[Album] = None,
    *,
    classify: bool = False,
    draft: bool = True,
    reminders: bool = False,
) -> None:
    """
    Background AI work for one user message: classify the first message (when
    categories are configured), then draft a reply. The category comes first,
    so the draft, its log record and the auto-reply mode can use it.
    """
    if user_data.message_thread_id is None:
        return

    data_urls = await _collect_images(config, message, album)
    if classify:
        key = await classify_message(provider, config, ai, message, data_urls)
        if key is not None:
            await apply_category(config, message, redis, user_data, ai, key)
    if draft:
        await run_ai_draft(
            provider, config, message, redis, user_data, ai.max_context_messages, album,
            ai=ai, data_urls=data_urls, reminders=reminders,
        )


async def _log_draft(
    redis: RedisStorage, ai: AISection, user_data: UserData, category: str | None, text: str, outcome: str
) -> None:
    """Write the draft to the log when ai.log_drafts is on; errors are only logged."""
    if not ai.log_drafts:
        return
    try:
        await redis.log_draft(user_data.id, user_data.message_thread_id, category, text, outcome)
    except Exception as ex:  # noqa: BLE001
        logger.warning("Failed to log the AI draft of user %s: %s", user_data.id, ex)


async def _auto_send(
    config: Config,
    message: Message,
    redis: RedisStorage,
    user_data: UserData,
    ai: AISection,
    category: str,
    draft: str,
    txt: TextMessage,
    reminders: bool = False,
) -> bool:
    """Send the draft straight to the user, as the Send button would. False if it did not go out."""
    try:
        await message.bot.send_message(chat_id=user_data.id, text=draft, parse_mode=None)
    except TelegramAPIError as ex:
        logger.warning("Auto-reply to user %s failed, leaving it as a draft: %s", user_data.id, ex)
        return False

    await redis.append_conversation(user_data.id, "assistant", draft)
    # An older draft's Send button must not resend anything now.
    await redis.clear_ai_draft(user_data.id)
    await _log_draft(redis, ai, user_data, category, draft, "auto_sent")
    if reminders:
        await end_reply_wait(redis, user_data.id)
    header = txt.get("ai_auto_sent_header").format(category=category_label(ai, category))
    with suppress(TelegramBadRequest):
        await message.bot.send_message(
            chat_id=config.bot.GROUP_ID,
            message_thread_id=user_data.message_thread_id,
            text=f"{header}\n\n{draft}",
            parse_mode=None,
        )
    return True


async def run_ai_draft(
    provider: LLMProvider,
    config: Config,
    message: Message,
    redis: RedisStorage,
    user_data: UserData,
    max_context: int,
    album: Optional[Album] = None,
    *,
    ai: AISection | None = None,
    data_urls: list | None = None,
    reminders: bool = False,
) -> None:
    """
    Draft a suggested reply based on the conversation so far and post it into
    the user's topic with Send/Skip buttons. Best-effort: failures are logged.

    With the auto-reply mode on for the user's category the draft goes to the
    user right away and the topic only gets a copy.
    ``reminders``: such an automatic reply ends the user's wait for a reply.
    """
    if user_data.message_thread_id is None:
        return
    ai = ai or AISection()

    # Support requests are often a bare screenshot, so the images have to reach
    # the model too; the transcript in storage only ever holds text.
    if data_urls is None:
        data_urls = await _collect_images(config, message, album)

    history = await redis.get_conversation(user_data.id, max_context)
    if not history:
        text = message_text(message)
        if not text.strip() and not data_urls:
            return
        history = [{"role": "user", "content": text}] if text.strip() else []

    # Reply in the user's language; if it is unclear, fall back to the language
    # the user selected in the bot (language_code).
    lang = user_data.language_code or "ru"
    lang_name = {"ru": "Russian", "en": "English"}.get(lang, lang)
    txt = TextMessage(lang)
    system_prompt = _resolve_system_prompt(config.ai)
    system_prompt += f"\n\nIf the user's language is unclear, reply in {lang_name}."
    messages = [{"role": "system", "content": system_prompt}, *history]
    if data_urls:
        messages = _with_images(messages, message_text(message), data_urls)

    # TIMEOUT_S bounds a single request; the overall limit has to leave room
    # for the client's own retries, or a 429 kills the draft mid-backoff.
    # This runs in a background task, so the long wait blocks nobody.
    try:
        draft = await asyncio.wait_for(
            provider.draft_reply(messages),
            timeout=config.ai.total_timeout_s,
        )
    except Exception as ex:  # noqa: BLE001 - best-effort, never block the pipeline
        # Provider errors embed dashboard URLs with key hashes and can be huge.
        logger.warning("AI draft failed: %s: %s", type(ex).__name__, redact(str(ex)))
        return

    if not draft:
        return

    category = None
    if ai.categories:
        try:
            category = await redis.get_user_category(user_data.id)
        except Exception as ex:  # noqa: BLE001
            logger.warning("Failed to read the category of user %s: %s", user_data.id, ex)

    # needs_human is checked again here: the config may have changed after the
    # mode was switched on. Silent mode means nothing reaches the user.
    configured = ai.category(category)
    if configured is not None and not configured.needs_human and not user_data.message_silent_mode:
        try:
            auto = await redis.get_auto_mode(configured.key)
        except Exception as ex:  # noqa: BLE001
            logger.warning("Failed to read the auto-reply mode of %s: %s", configured.key, ex)
            auto = False
        if auto and await _auto_send(
            config, message, redis, user_data, ai, configured.key, draft, txt, reminders,
        ):
            return

    await redis.set_ai_draft(user_data.id, draft)
    await _log_draft(redis, ai, user_data, category, draft, "pending")

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text=txt.get("ai_draft_send"), callback_data=f"ai:send:{user_data.id}"),
            InlineKeyboardButton(text=txt.get("ai_draft_skip"), callback_data=f"ai:skip:{user_data.id}"),
        ]]
    )

    header = txt.get("ai_draft_header")
    if category is not None:
        header += "\n" + txt.get("ai_draft_category").format(category=category_label(ai, category))

    with suppress(TelegramBadRequest):
        await message.bot.send_message(
            chat_id=config.bot.GROUP_ID,
            message_thread_id=user_data.message_thread_id,
            text=f"{header}\n\n{draft}",
            reply_markup=keyboard,
            parse_mode=None,
        )
