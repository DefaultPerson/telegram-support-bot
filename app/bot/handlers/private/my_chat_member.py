from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatMemberUpdated
from aiogram.utils.markdown import hlink

from app.bot.manager import Manager
from app.bot.policy import EvalContext, PolicyEngine
from app.bot.policy.context import EVENT_USER_STARTED, EVENT_USER_STOPPED
from app.bot.utils.redis import RedisStorage
from app.bot.utils.redis.models import UserData

router = Router()
router.my_chat_member.filter(F.chat.type == "private")


@router.my_chat_member()
async def handle_chat_member_update(
        update: ChatMemberUpdated,
        redis: RedisStorage,
        user_data: UserData,
        manager: Manager,
        policy_engine: PolicyEngine | None = None,
) -> None:
    """
    Handle updates of the bot chat member status.

    :param update: ChatMemberUpdated object.
    :param redis: RedisStorage object.
    :param user_data: UserData object.
    :param manager: Manager object.
    :param policy_engine: Optional policy engine (None when disabled).
    :return: None
    """
    # Update the user's state based on the new chat member status
    user_data.state = update.new_chat_member.status
    await redis.update_user(user_data.id, user_data)

    is_member = user_data.state == ChatMemberStatus.MEMBER

    # Let policy suppress the lifecycle notification in the group, if configured.
    if policy_engine is not None:
        event_type = EVENT_USER_STARTED if is_member else EVENT_USER_STOPPED
        decision = policy_engine.evaluate(
            EvalContext(event_type=event_type, language=user_data.language_code or "en")
        )
        if decision.suppress_group_notify:
            return

    if is_member:
        text = manager.text_message.get("user_restarted_bot")
    else:
        text = manager.text_message.get("user_stopped_bot")

    url = f"https://t.me/{user_data.username[1:]}" if user_data.username != "-" else f"tg://user?id={user_data.id}"

    await update.bot.send_message(
        chat_id=manager.config.bot.GROUP_ID,
        text=text.format(name=hlink(user_data.full_name, url)),
        message_thread_id=user_data.message_thread_id,
    )
