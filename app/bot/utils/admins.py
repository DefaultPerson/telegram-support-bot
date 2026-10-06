import logging

from aiogram import Bot

from app.config import Config


def is_admin(config: Config, user_id: int | None) -> bool:
    """True for the admins listed in BOT_DEV_IDS."""
    return user_id is not None and user_id in config.bot.DEV_IDS


def topic_link(group_id: int, message_thread_id: int) -> str:
    """Link to a forum topic of the support group (t.me/c/<id>/<thread>)."""
    chat = str(group_id)
    chat = chat[4:] if chat.startswith("-100") else chat.lstrip("-")
    return f"https://t.me/c/{chat}/{message_thread_id}"


def message_link(group_id: int, message_thread_id: int, message_id: int) -> str:
    """Link to a message in a forum topic of the support group (t.me/c/<id>/<thread>/<message>)."""
    return f"{topic_link(group_id, message_thread_id)}/{message_id}"


async def notify_admins(bot: Bot, config: Config, text: str) -> None:
    """Message every admin in BOT_DEV_IDS; one unreachable admin does not stop the rest."""
    for admin_id in config.bot.DEV_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text)
        except Exception as ex:  # noqa: BLE001 - best-effort notification
            logging.warning("Failed to notify admin %s: %s", admin_id, ex)
