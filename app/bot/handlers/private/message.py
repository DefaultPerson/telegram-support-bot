import asyncio

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.types import Message

from app.bot.llm import LLMProvider
from app.bot.manager import Manager
from app.bot.policy import PolicyEngine
from app.bot.types.album import Album
from app.bot.utils.create_forum_topic import (
    create_forum_topic,
    get_or_create_forum_topic,
)
from app.bot.utils.policy_runtime import (
    apply_auto_replies,
    apply_post_forward,
    build_message_context,
    run_ai_draft,
)
from app.bot.utils.redis import RedisStorage
from app.bot.utils.redis.models import UserData

router = Router()
router.message.filter(F.chat.type == "private", StateFilter(None))


@router.edited_message()
async def handle_edited_message(message: Message, manager: Manager) -> None:
    """
    Handle edited messages.

    :param message: The edited message.
    :param manager: Manager object.
    :return: None
    """
    # Get the text for the edited message
    text = manager.text_message.get("message_edited")
    # Reply to the edited message with the specified text
    msg = await message.reply(text)
    # Wait for 5 seconds before deleting the reply
    await asyncio.sleep(5)
    # Delete the reply to the edited message
    await msg.delete()


@router.message(F.media_group_id)
@router.message(F.media_group_id.is_(None))
async def handle_incoming_message(
        message: Message,
        manager: Manager,
        redis: RedisStorage,
        user_data: UserData,
        album: Album | None = None,
        policy_engine: PolicyEngine | None = None,
        llm_provider: LLMProvider | None = None,
) -> None:
    """
    Handles incoming messages and copies them to the forum topic.
    If the user is banned, the messages are ignored.

    :param message: The incoming message.
    :param manager: Manager object.
    :param redis: RedisStorage object.
    :param user_data: UserData object.
    :param album: Album object or None.
    :param policy_engine: Optional policy engine (None when disabled).
    :param llm_provider: Optional LLM provider (None when disabled).
    :return: None
    """
    # Check if the user is banned
    if user_data.is_banned:
        return

    # Whether this is the user's first contact (no topic yet).
    first_contact = user_data.message_thread_id is None

    # Evaluate declarative policy before forwarding, if enabled.
    decision = None
    if policy_engine is not None:
        decision = policy_engine.evaluate(build_message_context(message, user_data))
        await apply_auto_replies(decision, message)
        if decision.suppress_topic_creation:
            return

    async def copy_message_to_topic():
        """
        Copies the message or album to the forum topic.
        If no album is provided, the message is copied. Otherwise, the album is copied.
        """
        message_thread_id = await get_or_create_forum_topic(
            message.bot,
            redis,
            manager.config,
            user_data,
        )

        if not album:
            await message.forward(
                chat_id=manager.config.bot.GROUP_ID,
                message_thread_id=message_thread_id,
            )
        else:
            await album.copy_to(
                chat_id=manager.config.bot.GROUP_ID,
                message_thread_id=message_thread_id,
            )

    try:
        await copy_message_to_topic()
    except TelegramBadRequest as ex:
        if "message thread not found" in ex.message:
            user_data.message_thread_id = await create_forum_topic(
                message.bot,
                manager.config,
                user_data.full_name,
            )
            await redis.update_user(user_data.id, user_data)
            await copy_message_to_topic()
        else:
            raise

    # Apply post-forward policy side effects (tags, close, escalate).
    if decision is not None:
        await apply_post_forward(decision, message, redis, user_data, manager.config)

    # Offer an AI-drafted reply to the manager, if the LLM layer is enabled.
    if llm_provider is not None:
        only_first = policy_engine.ai.draft_only_first_message if policy_engine else True
        if first_contact or not only_first:
            categories = policy_engine.ai.categories if policy_engine else []
            asyncio.create_task(
                run_ai_draft(llm_provider, manager.config, categories, message, redis, user_data)
            )

    # Send a confirmation message to the user
    text = manager.text_message.get("message_sent")
    # Reply to the edited message with the specified text
    msg = await message.reply(text)
    # Wait for 5 seconds before deleting the reply
    await asyncio.sleep(5)
    # Delete the reply to the edited message
    await msg.delete()
