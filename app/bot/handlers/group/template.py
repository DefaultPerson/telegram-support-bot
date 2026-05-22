import asyncio
from contextlib import suppress

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, MagicData
from aiogram.types import Message

from app.bot.manager import Manager
from app.bot.policy import PolicyEngine
from app.bot.policy.actions import render_template
from app.bot.utils.redis import RedisStorage

router = Router()
router.message.filter(
    F.message_thread_id.is_not(None),
    F.chat.type.in_(["group", "supergroup"]),
    MagicData(F.event_chat.id == F.config.bot.GROUP_ID),  # type: ignore
)


@router.message(Command("template"))
async def template_handler(
        message: Message,
        command: CommandObject,
        manager: Manager,
        redis: RedisStorage,
        policy_engine: PolicyEngine | None = None,
) -> None:
    """Send a predefined policy template to the user: /template <key>."""
    user_data = await redis.get_by_message_thread_id(message.message_thread_id)
    if not user_data: return None  # noqa

    if policy_engine is None:
        await message.reply(manager.text_message.get("policy_disabled"))
        return

    key = (command.args or "").strip()
    if not key:
        await message.reply(manager.text_message.get("template_usage"))
        return

    try:
        text = render_template(policy_engine.document, key, user_data.language_code or "en")
    except KeyError:
        await message.reply(manager.text_message.get("template_not_found").format(key=key))
        return

    try:
        await message.bot.send_message(chat_id=user_data.id, text=text)
    except TelegramBadRequest:
        err = await message.reply(manager.text_message.get("message_not_sent"))
        await asyncio.sleep(5)
        await err.delete()
        return

    # Show what was sent in the topic and record it for LLM context.
    await redis.append_conversation(user_data.id, "assistant", text)
    await message.reply(manager.text_message.get("template_sent").format(text=text))


@router.message(Command("close"))
async def close_handler(message: Message, redis: RedisStorage) -> None:
    """Mark the conversation closed and close the forum topic."""
    user_data = await redis.get_by_message_thread_id(message.message_thread_id)
    if not user_data: return None  # noqa

    user_data.status = "closed"
    await redis.update_user(user_data.id, user_data)
    with suppress(TelegramBadRequest):
        await message.bot.close_forum_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
        )


@router.message(Command("escalate"))
async def escalate_handler(message: Message, manager: Manager, redis: RedisStorage) -> None:
    """Mark the conversation escalated and notify the developer."""
    user_data = await redis.get_by_message_thread_id(message.message_thread_id)
    if not user_data: return None  # noqa

    user_data.status = "escalated"
    await redis.update_user(user_data.id, user_data)
    with suppress(Exception):
        await message.bot.send_message(
            chat_id=manager.config.bot.DEV_ID,
            text=manager.text_message.get("escalated_dev").format(
                full_name=user_data.full_name, id=user_data.id),
        )
    await message.reply(manager.text_message.get("escalated"))
