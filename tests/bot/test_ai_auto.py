import asyncio
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramForbiddenError

from app.bot.handlers.group import ai as group_ai
from app.bot.policy import load_policy_from_dict
from app.bot.utils.policy_runtime import run_ai_draft
from app.bot.utils.redis.models import UserData

ADMIN = 1
GROUP = -100

CATEGORIES = [
    {"key": "payout", "title": "Payouts", "icon": "💸"},
    {"key": "refund", "title": "Refunds", "needs_human": True},
]


def engine(**ai):
    return load_policy_from_dict({"ai": {"log_drafts": True, "categories": CATEGORIES, **ai}})


def row(sent, skipped=0, replied=0):
    return {"total": sent + skipped + replied, "sent": sent, "skipped": skipped,
            "manager_replied": replied, "auto_sent": 0}


class _Storage:
    def __init__(self, category=None, auto=(), stats=None) -> None:
        self.category = category
        self.modes = {key: True for key in auto}
        self.stats = stats or {}
        self.mode_changes: list[tuple] = []
        self.logged: list[tuple] = []
        self.conversation: list[tuple] = []
        self.draft = "older draft"

    async def get_user_category(self, user_id):
        return self.category

    async def get_auto_mode(self, category):
        return self.modes.get(category, False)

    async def get_auto_modes(self):
        return dict(self.modes)

    async def set_auto_mode(self, category, enabled, updated_by):
        self.modes[category] = enabled
        self.mode_changes.append((category, enabled, updated_by))

    async def get_draft_stats(self, days=None):
        return self.stats

    async def get_conversation(self, user_id, limit):
        return [{"role": "user", "content": "where is my payout?"}]

    async def set_ai_draft(self, user_id, text):
        self.draft = text

    async def clear_ai_draft(self, user_id):
        self.draft = None

    async def append_conversation(self, user_id, role, text):
        self.conversation.append((role, text))

    async def log_draft(self, user_id, thread_id, category, text, outcome="pending"):
        self.logged.append((category, outcome))


class _Bot:
    def __init__(self, blocked=False) -> None:
        self.sent: list[dict] = []
        self.blocked = blocked

    async def send_message(self, **kwargs):
        if self.blocked and kwargs["chat_id"] == 42:
            raise TelegramForbiddenError(method=None, message="bot was blocked by the user")
        self.sent.append(kwargs)


class _Provider:
    async def draft_reply(self, messages):
        return "Payouts go out on Fridays."


def make_user(silent=False) -> UserData:
    return UserData(
        message_thread_id=7, message_silent_id=None, message_silent_mode=silent,
        id=42, full_name="User", username="-", language_code="en",
    )


def draft(storage, policy, bot=None, silent=False):
    from app.config import AIConfig

    bot = bot or _Bot()
    ai = AIConfig(
        PROVIDER="openai_compatible", BASE_URL="", API_KEY="k", MODEL="m",
        SYSTEM_PROMPT_PATH="", TIMEOUT_S=5, VISION=False,
    )
    config = SimpleNamespace(ai=ai, bot=SimpleNamespace(GROUP_ID=GROUP, DEV_IDS=[ADMIN]))
    message = SimpleNamespace(bot=bot, text="where is my payout?", caption=None)
    asyncio.run(run_ai_draft(_Provider(), config, message, storage, make_user(silent), 12, ai=policy.ai))
    return bot


def test_auto_mode_sends_the_draft_to_the_user():
    storage = _Storage(category="payout", auto=["payout"])

    bot = draft(storage, engine())

    to_user, to_topic = bot.sent
    assert to_user == {"chat_id": 42, "text": "Payouts go out on Fridays.", "parse_mode": None}
    assert to_topic["message_thread_id"] == 7
    assert "Auto-reply" in to_topic["text"] and "💸 Payouts" in to_topic["text"]
    assert "reply_markup" not in to_topic
    assert storage.logged == [("payout", "auto_sent")]
    assert storage.conversation == [("assistant", "Payouts go out on Fridays.")]
    # An older draft's Send button has nothing left to send.
    assert storage.draft is None


@pytest.mark.parametrize("storage,silent", [
    (_Storage(category="payout"), False),                    # mode off (the default)
    (_Storage(category="refund", auto=["refund"]), False),   # needs a human
    (_Storage(category="payout", auto=["payout"]), True),    # silent mode
    (_Storage(category=None, auto=["payout"]), False),       # no category
])
def test_draft_waits_for_the_manager_otherwise(storage, silent):
    bot = draft(storage, engine(), silent=silent)

    assert [m["chat_id"] for m in bot.sent] == [GROUP]
    assert "reply_markup" in bot.sent[0]
    assert storage.logged[-1][1] == "pending"


def test_failed_auto_send_falls_back_to_a_draft():
    storage = _Storage(category="payout", auto=["payout"])

    bot = draft(storage, engine(), bot=_Bot(blocked=True))

    assert [m["chat_id"] for m in bot.sent] == [GROUP]
    assert "reply_markup" in bot.sent[0]
    assert storage.logged == [("payout", "pending")]


class _Message:
    def __init__(self, user_id=ADMIN) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.replies: list[tuple] = []

    async def reply(self, text, reply_markup=None):
        self.replies.append((text, reply_markup))


class _Text(str):
    """A text key that shows what it was formatted with."""

    def format(self, *args, **kwargs):
        return " ".join([self, *(f"{k}={v}" for k, v in kwargs.items())])


def manager():
    return SimpleNamespace(
        config=SimpleNamespace(bot=SimpleNamespace(DEV_IDS=[ADMIN])),
        text_message=SimpleNamespace(get=_Text),
    )


def auto_command(args, policy, storage, user_id=ADMIN):
    message = _Message(user_id)
    asyncio.run(group_ai.ai_auto_handler(message, SimpleNamespace(args=args), manager(), storage, policy))
    return message.replies


def test_ai_auto_is_admin_only():
    replies = auto_command("payout on", engine(), _Storage(), user_id=999)
    assert replies[0][0].startswith("ai_admins_only")


def test_ai_auto_without_categories():
    replies = auto_command("", load_policy_from_dict({}), _Storage())
    assert replies[0][0].startswith("ai_auto_no_categories")


def test_ai_auto_lists_modes_and_stats():
    storage = _Storage(auto=["payout"], stats={"payout": row(19, 1)})

    text = auto_command("", engine(), storage)[0][0]

    payout, refund = text.split("\n")[1:]
    assert "💸 Payouts" in payout and "ai_auto_on" in payout and "95%" in payout
    assert "ai_auto_human_only" in refund


def test_needs_human_category_cannot_be_enabled():
    storage = _Storage()

    text, keyboard = auto_command("refund on", engine(), storage)[0]

    assert text.startswith("ai_auto_needs_human")
    assert keyboard is None
    assert storage.mode_changes == []


def test_enabling_asks_for_confirmation_and_warns_below_the_bar():
    storage = _Storage(stats={"payout": row(9, 1)})

    text, keyboard = auto_command("payout on", engine(), storage)[0]

    assert text.startswith("ai_auto_confirm_on")
    assert "ai_auto_warning" in text
    confirm, cancel = keyboard.inline_keyboard[0]
    assert confirm.callback_data == "ai_auto:on:payout"
    assert cancel.callback_data == "ai_auto:cancel"
    # Nothing changes until an admin confirms.
    assert storage.mode_changes == []


def test_no_warning_above_the_bar():
    storage = _Storage(stats={"payout": row(19, 1)})
    text, _ = auto_command("payout on", engine(), storage)[0]
    assert "ai_auto_warning" not in text


def test_bar_comes_from_the_config():
    storage = _Storage(stats={"payout": row(4, 1)})
    text, _ = auto_command("payout on", engine(auto_threshold=0.8, auto_min_drafts=5), storage)[0]
    assert "ai_auto_warning" not in text


@pytest.mark.parametrize("args", ["payout", "payout maybe", "nope on"])
def test_bad_arguments_change_nothing(args):
    storage = _Storage()
    text, keyboard = auto_command(args, engine(), storage)[0]
    assert keyboard is None
    assert storage.mode_changes == []


class _CallbackMessage:
    def __init__(self) -> None:
        self.text = None

    async def edit_text(self, text, reply_markup=None):
        self.text = text


class _Call:
    def __init__(self, data, user_id=ADMIN) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = _CallbackMessage()
        self.answers: list = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)


def press(data, policy, storage, user_id=ADMIN):
    call = _Call(data, user_id)
    asyncio.run(group_ai.ai_auto_callback(call, manager(), storage, policy))
    return call


def test_confirm_switches_the_mode():
    storage = _Storage()

    call = press("ai_auto:on:payout", engine(), storage)

    assert storage.mode_changes == [("payout", True, ADMIN)]
    assert call.message.text.startswith("ai_auto_enabled")

    press("ai_auto:off:payout", engine(), storage)
    assert storage.modes["payout"] is False


def test_only_an_admin_can_confirm():
    storage = _Storage()
    press("ai_auto:on:payout", engine(), storage, user_id=999)
    assert storage.mode_changes == []


def test_cancel_changes_nothing():
    storage = _Storage()
    call = press("ai_auto:cancel", engine(), storage)
    assert storage.mode_changes == []
    assert call.message.text.startswith("ai_auto_cancelled")


def test_confirm_rechecks_needs_human():
    storage = _Storage()
    press("ai_auto:on:refund", engine(), storage)
    assert storage.mode_changes == []
