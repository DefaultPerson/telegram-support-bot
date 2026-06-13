from aiogram import F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.types import Message
from aiogram_broadcast import BaseBroadcastStorage
from aiogram_broadcast.ui import BroadcastUIManager

from app.bot.handlers.private.windows import Window
from app.bot.manager import Manager
from app.bot.utils.create_forum_topic import get_or_create_forum_topic
from app.bot.utils.redis import RedisStorage
from app.bot.utils.redis.models import UserData
from app.config import Config

router = Router()
router.message.filter(F.chat.type == "private")


class IsDev(BaseFilter):
    """Allow only configured developer/admin IDs (``BOT_DEV_IDS``)."""

    async def __call__(self, message: Message, config: Config) -> bool:
        return message.from_user is not None and message.from_user.id in config.bot.DEV_IDS


@router.message(Command("start"))
async def handler(
        message: Message,
        manager: Manager,
        redis: RedisStorage,
        user_data: UserData,
) -> None:
    """
    Handles the /start command.

    If the user has already selected a language, displays the main menu window.
    Otherwise, prompts the user to select a language.

    :param message: Message object.
    :param manager: Manager object.
    :param redis: RedisStorage object.
    :param user_data: UserData object.
    :return: None
    """
    if user_data.language_code:
        await Window.main_menu(manager)
    else:
        await Window.select_language(manager)
    await manager.delete_message(message)

    # Create the forum topic
    await get_or_create_forum_topic(message.bot, redis, manager.config, user_data)


@router.message(Command("language"))
async def handler(message: Message, manager: Manager, user_data: UserData) -> None:
    """
    Handles the /language command.

    If the user has already selected a language, prompts the user to select a new language.
    Otherwise, prompts the user to select a language.

    :param message: Message object.
    :param manager: Manager object.
    :param user_data: UserData object.
    :return: None
    """
    if user_data.language_code:
        await Window.change_language(manager)
    else:
        await Window.select_language(manager)
    await manager.delete_message(message)


@router.message(Command("newsletter"), IsDev())
async def handler(
        message: Message,
        manager: Manager,
        broadcast_ui: BroadcastUIManager,
        broadcast_storage: BaseBroadcastStorage,
) -> None:
    """
    Handles the /newsletter command — opens the broadcast UI for admins.

    :param message: Message object.
    :param manager: Manager object.
    :param broadcast_ui: Broadcast UI manager (from aiogram-broadcast).
    :param broadcast_storage: Broadcast subscriber storage.
    :return: None
    """
    subscriber_ids = await broadcast_storage.get_all_subscriber_ids()
    await broadcast_ui.open_menu(subscriber_ids)
    await manager.delete_message(message)
