import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)

from app.bot.utils.texts import SUPPORTED_LANGUAGES
from app.config import Config


async def setup(bot: Bot, config: Config) -> None:
    """
    Set up bot commands for various scopes and languages.

    :param bot: The Bot object.
    :param config: The Config object.
    """
    # Define bot commands for different languages
    commands = {
        "en": [
            BotCommand(command="start", description="Restart bot"),
        ],
        "ru": [
            BotCommand(command="start", description="Перезапустить бота"),
        ]
    }

    if len(SUPPORTED_LANGUAGES) > 1:
        # If there are more than one supported language, add commands for changing the language
        commands["en"].append(
            BotCommand(command="language", description="Change language"),
        )
        commands["ru"].append(
            BotCommand(command="language", description="Изменить язык"),
        )

    group_commands = {
        "en": [
            BotCommand(command="ban", description="Block/Unblock a user"),
            BotCommand(command="silent", description="Activate/Deactivate silent Mode"),
            BotCommand(command="information", description="User information"),
            BotCommand(command="template", description="Send a template reply: /template <key>"),
            BotCommand(command="close", description="Close the conversation"),
            BotCommand(command="escalate", description="Escalate the conversation"),
        ],
        "ru": [
            BotCommand(command="ban", description="Заблокировать/Разблокировать пользователя"),
            BotCommand(command="silent", description="Активировать/Деактивировать тихий режим"),
            BotCommand(command="information", description="Информация о пользователе"),
            BotCommand(command="template", description="Отправить шаблон: /template <ключ>"),
            BotCommand(command="close", description="Закрыть диалог"),
            BotCommand(command="escalate", description="Эскалировать диалог"),
        ]
    }

    admin_commands = {
        "en":
            commands["en"].copy() +
            [BotCommand(command="newsletter", description="Newsletter menu")],
        "ru":
            commands["ru"].copy() +
            [BotCommand(command="newsletter", description="Меню рассылки")],
    }

    # Set commands for all private chats (set first so a DEV-chat failure below
    # cannot skip refreshing the public/group command menus).
    await bot.set_my_commands(
        commands=commands["en"],
        scope=BotCommandScopeAllPrivateChats(),
    )
    await bot.set_my_commands(
        commands=commands["ru"],
        scope=BotCommandScopeAllPrivateChats(),
        language_code="ru",
    )
    # Set commands for all group chats.
    await bot.set_my_commands(
        commands=group_commands["en"],
        scope=BotCommandScopeAllGroupChats(),
    )
    await bot.set_my_commands(
        commands=group_commands["ru"],
        scope=BotCommandScopeAllGroupChats(),
        language_code="ru",
    )

    # Set commands for each dev/admin (adds /newsletter to the private menu).
    # An admin who has not started the bot yet is skipped, not fatal.
    for dev_id in config.bot.DEV_IDS:
        try:
            await bot.set_my_commands(
                commands=admin_commands["en"],
                scope=BotCommandScopeChat(chat_id=dev_id),
            )
            await bot.set_my_commands(
                commands=admin_commands["ru"],
                scope=BotCommandScopeChat(chat_id=dev_id),
                language_code="ru",
            )
        except TelegramBadRequest:
            logging.warning("Admin chat %s not found; skipping command setup.", dev_id)


async def delete(bot: Bot, config: Config) -> None:
    """
    Delete bot commands for various scopes and languages.

    :param config: The Config object.
    :param bot: The Bot object.
    """

    # Delete dev/admin command scopes (skip admins that never started the bot).
    for dev_id in config.bot.DEV_IDS:
        try:
            await bot.delete_my_commands(
                scope=BotCommandScopeChat(chat_id=dev_id),
            )
            await bot.delete_my_commands(
                scope=BotCommandScopeChat(chat_id=dev_id),
                language_code="ru",
            )
        except TelegramBadRequest:
            logging.warning("Admin chat %s not found; skipping command deletion.", dev_id)

    # Delete commands for all private chats in any language
    await bot.delete_my_commands(
        scope=BotCommandScopeAllPrivateChats(),
    )
    # Delete commands for all private chats in Russian language
    await bot.delete_my_commands(
        scope=BotCommandScopeAllPrivateChats(),
        language_code="ru",
    )
    # Delete commands for all group chats in any language
    await bot.delete_my_commands(
        scope=BotCommandScopeAllGroupChats(),
    )
    # Delete commands for all group chats in Russian language
    await bot.delete_my_commands(
        scope=BotCommandScopeAllGroupChats(),
        language_code="ru",
    )
