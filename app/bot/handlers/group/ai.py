import html
from contextlib import suppress

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, MagicData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.manager import Manager
from app.bot.policy import PolicyEngine
from app.bot.policy.schema import AICategory, AISection
from app.bot.utils.admins import is_admin
from app.bot.utils.policy_runtime import category_label
from app.bot.utils.redis import RedisStorage

# Works in any topic of the support group, General included.
router = Router()
router.message.filter(
    F.chat.type.in_(["group", "supergroup"]),
    MagicData(F.event_chat.id == F.config.bot.GROUP_ID),  # type: ignore
)
router.callback_query.filter(
    F.message.chat.type.in_(["group", "supergroup"]),
    MagicData(F.event_chat.id == F.config.bot.GROUP_ID),  # type: ignore
)

EMPTY_STATS = {"total": 0, "sent": 0, "skipped": 0, "manager_replied": 0, "auto_sent": 0}


def _ai(policy_engine: PolicyEngine | None) -> AISection:
    """The policy's AI options, or the defaults (everything off) without a policy."""
    return policy_engine.ai if policy_engine is not None else AISection()


def reviewed(stats: dict[str, int]) -> int:
    """Drafts a manager acted on; pending, superseded and auto-sent ones are not verdicts."""
    return stats["sent"] + stats["skipped"] + stats["manager_replied"]


def sent_rate(stats: dict[str, int]) -> float | None:
    """Share of reviewed drafts that were sent as they were (None before any review)."""
    count = reviewed(stats)
    return stats["sent"] / count if count else None


def _percent(rate: float | None) -> str:
    return "—" if rate is None else f"{rate:.0%}"


def _label(manager: Manager, ai: AISection, key: str | None) -> str:
    return html.escape(category_label(ai, key) or manager.text_message.get("no_category"))


def _category_stats(manager: Manager, stats: dict[str, int]) -> str:
    return manager.text_message.get("ai_auto_stats").format(
        reviewed=reviewed(stats), sent=stats["sent"], rate=_percent(sent_rate(stats)),
    )


def below_bar(ai: AISection, stats: dict[str, int]) -> bool:
    """True when the category has too few reviewed drafts or too low a sent share."""
    rate = sent_rate(stats)
    return reviewed(stats) < ai.auto_min_drafts or rate is None or rate < ai.auto_threshold


@router.message(Command("ai_auto"))
async def ai_auto_handler(
        message: Message,
        command: CommandObject,
        manager: Manager,
        redis: RedisStorage,
        policy_engine: PolicyEngine | None = None,
) -> None:
    """
    Automatic replies per category. Admins only.

    /ai_auto lists the categories; /ai_auto <key> on|off asks for confirmation,
    and the mode changes only when an admin presses Confirm.
    """
    if not is_admin(manager.config, message.from_user and message.from_user.id):
        await message.reply(manager.text_message.get("ai_admins_only"))
        return

    ai = _ai(policy_engine)
    if not ai.categories:
        await message.reply(manager.text_message.get("ai_auto_no_categories"))
        return

    stats = await redis.get_draft_stats()
    args = (command.args or "").split()

    if not args:
        modes = await redis.get_auto_modes()
        lines = [manager.text_message.get("ai_auto_list_header")]
        for category in ai.categories:
            if category.needs_human:
                mode = manager.text_message.get("ai_auto_human_only")
            else:
                mode = manager.text_message.get("ai_auto_on" if modes.get(category.key) else "ai_auto_off")
            lines.append(
                manager.text_message.get("ai_auto_list_row").format(
                    category=_label(manager, ai, category.key),
                    key=html.escape(category.key),
                    mode=mode,
                    stats=_category_stats(manager, stats.get(category.key, EMPTY_STATS)),
                )
            )
        await message.reply("\n".join(lines))
        return

    if len(args) != 2 or args[1].lower() not in ("on", "off"):
        await message.reply(manager.text_message.get("ai_auto_usage"))
        return

    key, state = args[0], args[1].lower()
    category = ai.category(key)
    if category is None:
        await message.reply(manager.text_message.get("ai_auto_unknown").format(key=html.escape(key)))
        return
    if state == "on" and category.needs_human:
        await message.reply(
            manager.text_message.get("ai_auto_needs_human").format(category=_label(manager, ai, key))
        )
        return

    await message.reply(
        confirmation_text(manager, ai, category, state == "on", stats.get(key, EMPTY_STATS)),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text=manager.text_message.get("ai_auto_confirm"),
                    callback_data=f"ai_auto:{state}:{key}",
                ),
                InlineKeyboardButton(
                    text=manager.text_message.get("ai_auto_cancel"),
                    callback_data="ai_auto:cancel",
                ),
            ]]
        ),
    )


def confirmation_text(
    manager: Manager, ai: AISection, category: AICategory, enable: bool, stats: dict[str, int]
) -> str:
    """The question /ai_auto asks, with the category's stats and, when enabling, a warning below the bar."""
    label = _label(manager, ai, category.key)
    text = manager.text_message.get("ai_auto_confirm_on" if enable else "ai_auto_confirm_off").format(
        category=label, stats=_category_stats(manager, stats),
    )
    if enable and below_bar(ai, stats):
        text += "\n\n" + manager.text_message.get("ai_auto_warning").format(
            rate=_percent(sent_rate(stats)),
            threshold=_percent(ai.auto_threshold),
            reviewed=reviewed(stats),
            min_drafts=ai.auto_min_drafts,
        )
    return text


@router.callback_query(F.data.startswith("ai_auto:"))
async def ai_auto_callback(
        call: CallbackQuery,
        manager: Manager,
        redis: RedisStorage,
        policy_engine: PolicyEngine | None = None,
) -> None:
    """Apply (or drop) an /ai_auto change once an admin confirms it."""
    if not is_admin(manager.config, call.from_user.id):
        await call.answer(manager.text_message.get("ai_admins_only"), show_alert=True)
        return

    parts = call.data.split(":", 2)
    if parts[1:] == ["cancel"]:
        with suppress(TelegramBadRequest):
            await call.message.edit_text(manager.text_message.get("ai_auto_cancelled"))
        await call.answer()
        return
    if len(parts) != 3 or parts[1] not in ("on", "off"):
        await call.answer()
        return

    ai = _ai(policy_engine)
    category = ai.category(parts[2])
    enable = parts[1] == "on"
    # The config may have changed since the question was asked.
    if category is None or (enable and category.needs_human):
        with suppress(TelegramBadRequest):
            await call.message.edit_text(manager.text_message.get("ai_auto_cancelled"))
        await call.answer()
        return

    await redis.set_auto_mode(category.key, enable, call.from_user.id)
    text = manager.text_message.get("ai_auto_enabled" if enable else "ai_auto_disabled").format(
        category=_label(manager, ai, category.key),
    )
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text)
    await call.answer()
