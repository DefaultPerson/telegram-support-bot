from contextlib import suppress

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import MagicData
from aiogram.types import CallbackQuery

from app.bot.manager import Manager
from app.bot.policy import PolicyEngine
from app.bot.utils.redis import RedisStorage
from app.bot.utils.reminders import end_reply_wait, reminders_enabled

router = Router()
router.callback_query.filter(
    F.message.chat.type.in_(["group", "supergroup"]),
    MagicData(F.event_chat.id == F.config.bot.GROUP_ID),  # type: ignore
)


@router.callback_query(F.data.startswith("ai:"))
async def ai_draft_callback(
        call: CallbackQuery,
        manager: Manager,
        redis: RedisStorage,
        policy_engine: PolicyEngine | None = None,
) -> None:
    """Handle the Send/Skip buttons attached to an AI draft suggestion."""
    log_drafts = policy_engine is not None and policy_engine.ai.log_drafts
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

    if action not in ("send", "skip"):
        await call.answer()
        return

    draft = await redis.get_ai_draft(user_id)
    # A newer draft replaced the one this message shows: its buttons must not
    # send or drop the newer one. Drafts stored without a message id act as before.
    if draft is not None and draft[1] is not None and draft[1] != call.message.message_id:
        await call.answer(manager.text_message.get("draft_stale"))
        with suppress(TelegramBadRequest):
            await call.message.edit_reply_markup(reply_markup=None)
        return

    if action == "send":
        if draft:
            text = draft[0]
            try:
                await call.bot.send_message(chat_id=user_id, text=text, parse_mode=None)
                await redis.append_conversation(user_id, "assistant", text)
                if log_drafts:
                    await redis.resolve_draft(user_id, "sent")
                if reminders_enabled(policy_engine):
                    await end_reply_wait(redis, user_id)
                await call.answer(manager.text_message.get("draft_sent"))
            except TelegramBadRequest:
                await call.answer(manager.text_message.get("draft_send_failed"), show_alert=True)
        else:
            await call.answer(manager.text_message.get("draft_expired"))
        await redis.clear_ai_draft(user_id)
        with suppress(TelegramBadRequest):
            await call.message.edit_reply_markup(reply_markup=None)

    else:
        await redis.clear_ai_draft(user_id)
        if log_drafts:
            await redis.resolve_draft(user_id, "skipped")
        with suppress(TelegramBadRequest):
            await call.message.delete()
        await call.answer(manager.text_message.get("draft_skipped"))
