from contextlib import suppress

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import MagicData
from aiogram.types import CallbackQuery

from app.bot.manager import Manager
from app.bot.utils.redis import RedisStorage

router = Router()
router.callback_query.filter(
    F.message.chat.type.in_(["group", "supergroup"]),
    MagicData(F.event_chat.id == F.config.bot.GROUP_ID),  # type: ignore
)


@router.callback_query(F.data.startswith("ai:"))
async def ai_draft_callback(call: CallbackQuery, manager: Manager, redis: RedisStorage) -> None:
    """Handle the Send/Skip buttons attached to an AI draft suggestion."""
    parts = call.data.split(":")
    if len(parts) != 3:
        await call.answer()
        return

    _, action, raw_user_id = parts
    try:
        user_id = int(raw_user_id)
    except ValueError:
        await call.answer()
        return

    if action == "send":
        draft = await redis.get_ai_draft(user_id)
        if draft:
            try:
                await call.bot.send_message(chat_id=user_id, text=draft, parse_mode=None)
                await redis.append_conversation(user_id, "assistant", draft)
                await call.answer(manager.text_message.get("draft_sent"))
            except TelegramBadRequest:
                await call.answer(manager.text_message.get("draft_send_failed"), show_alert=True)
        else:
            await call.answer(manager.text_message.get("draft_expired"))
        await redis.clear_ai_draft(user_id)
        with suppress(TelegramBadRequest):
            await call.message.edit_reply_markup(reply_markup=None)

    elif action == "skip":
        await redis.clear_ai_draft(user_id)
        with suppress(TelegramBadRequest):
            await call.message.delete()
        await call.answer(manager.text_message.get("draft_skipped"))

    else:
        await call.answer()
