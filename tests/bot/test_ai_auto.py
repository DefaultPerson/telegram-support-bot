import asyncio
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramForbiddenError

from app.bot.handlers.group import ai as group_ai
from app.bot.policy import load_policy_from_dict
from app.bot.policy.schema import AICannedReply, normalize_reply
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
        self.waits_ended: list[int] = []

    async def get_user_category(self, user_id):
        return self.category

    async def get_auto_mode(self, category):
        return self.modes.get(category, False)

    async def get_auto_modes(self):
        return dict(self.modes)

    async def set_auto_mode(self, category, enabled, updated_by):
        self.modes[category] = enabled
        self.mode_changes.append((category, enabled, updated_by))

    async def get_draft_stats(self):
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

    async def end_reply_wait(self, user_id):
        self.waits_ended.append(user_id)


class _Bot:
    def __init__(self, blocked=False) -> None:
        self.sent: list[dict] = []
        self.blocked = blocked
        self.blocked_attempts = 0

    async def send_message(self, **kwargs):
        if self.blocked and kwargs["chat_id"] == 42:
            self.blocked_attempts += 1
            raise TelegramForbiddenError(method=None, message="bot was blocked by the user")
        self.sent.append(kwargs)


class _Provider:
    def __init__(self, answer="Payouts go out on Fridays.") -> None:
        self.answer = answer
        self.messages: list[dict] = []

    async def draft_reply(self, messages):
        self.messages = messages
        return self.answer


def make_user(silent=False, language="en") -> UserData:
    return UserData(
        message_thread_id=7, message_silent_id=None, message_silent_mode=silent,
        id=42, full_name="User", username="-", language_code=language,
    )


def draft(storage, policy, bot=None, silent=False, provider=None, language="en", reminders=False):
    from app.config import AIConfig

    bot = bot or _Bot()
    provider = provider or _Provider()
    ai = AIConfig(
        PROVIDER="openai_compatible", BASE_URL="", API_KEY="k", MODEL="m",
        SYSTEM_PROMPT_PATH="", TIMEOUT_S=5, VISION=False,
    )
    config = SimpleNamespace(ai=ai, bot=SimpleNamespace(GROUP_ID=GROUP, DEV_IDS=[ADMIN]))
    message = SimpleNamespace(bot=bot, text="where is my payout?", caption=None)
    asyncio.run(run_ai_draft(
        provider, config, message, storage, make_user(silent, language), 12,
        ai=policy.ai, reminders=reminders,
    ))
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


CANNED = [
    {"key": "payday", "en": "Payouts go out on Fridays.", "ru": "Всё выплачиваем по пятницам."},
    {"key": "wait", "en": "Please wait, a manager will reply soon."},
]


def test_canned_replies_are_off_by_default():
    assert load_policy_from_dict({}).ai.canned_replies == []


@pytest.mark.parametrize("text,expected", [
    ("  Ёлка   стоит.\n", "елка стоит"),
    ("Done!!", "done"),
    ("Done ?", "done ?"),
    ("Done. ", "done"),
])
def test_normalize_reply(text, expected):
    assert normalize_reply(text) == expected


@pytest.mark.parametrize("reply", [
    {"key": "выплаты", "en": "Hi"},
    {"key": "k" * 41, "en": "Hi"},
    {"key": "hi"},
    {"key": "hi", "en": "  "},
    {"key": "hi", "en": "Hi", "ru": "!"},
    {"key": "hi", "de": "Hallo"},
])
def test_bad_canned_reply_is_rejected(reply):
    with pytest.raises(ValueError):
        AICannedReply(**reply)


def test_repeated_canned_keys_are_rejected():
    with pytest.raises(ValueError):
        engine(canned_replies=[{"key": "hi", "en": "Hi"}, {"key": "hi", "ru": "Привет"}])


@pytest.mark.parametrize("answer", [
    "Payouts go out on Fridays.",
    "  payouts  go out\non FRIDAYS!\n",
    "Payouts go out on Fridays",
    "Все выплачиваем по пятницам",      # the other language, ё written as е
])
def test_canned_reply_is_sent_right_away(answer):
    storage = _Storage()

    bot = draft(storage, engine(canned_replies=CANNED), provider=_Provider(answer), reminders=True)

    to_user, to_topic = bot.sent
    assert to_user == {"chat_id": 42, "text": answer, "parse_mode": None}
    assert to_topic["message_thread_id"] == 7
    assert to_topic["text"].startswith("🤖 Auto-reply sent to the user (canned reply payday):")
    assert "reply_markup" not in to_topic
    assert storage.logged == [(None, "auto_sent")]
    assert storage.conversation == [("assistant", answer)]
    assert storage.draft is None
    # Not an answer: the admins are still reminded if nobody replies.
    assert storage.waits_ended == []


def test_canned_reply_ignores_the_category():
    # needs_human and the mode being off do not hold back a pre-approved text.
    storage = _Storage(category="refund")

    bot = draft(storage, engine(canned_replies=CANNED))

    assert [m["chat_id"] for m in bot.sent] == [42, GROUP]
    assert storage.logged == [("refund", "auto_sent")]


def test_canned_reply_header_is_localized():
    bot = draft(_Storage(), engine(canned_replies=CANNED), provider=_Provider("Всё выплачиваем по пятницам."),
                language="ru")

    assert bot.sent[1]["text"].startswith("🤖 Пользователю отправлен автоответ (заготовка payday):")


@pytest.mark.parametrize("answer", [
    "Payouts go out on Fridays. Anything else?",
    "Payouts go out on Mondays.",
    "Payouts go out on Fridays?",
])
def test_other_text_stays_a_draft(answer):
    storage = _Storage()

    bot = draft(storage, engine(canned_replies=CANNED), provider=_Provider(answer))

    assert [m["chat_id"] for m in bot.sent] == [GROUP]
    assert "reply_markup" in bot.sent[0]
    assert storage.logged == [(None, "pending")]


def test_other_text_still_follows_the_category_mode():
    storage = _Storage(category="payout", auto=["payout"])

    bot = draft(storage, engine(canned_replies=CANNED), provider=_Provider("Fridays, usually."))

    assert [m["chat_id"] for m in bot.sent] == [42, GROUP]
    assert "category" in bot.sent[1]["text"]
    assert storage.logged == [("payout", "auto_sent")]


def test_canned_reply_waits_in_silent_mode():
    storage = _Storage()

    bot = draft(storage, engine(canned_replies=CANNED), silent=True)

    assert [m["chat_id"] for m in bot.sent] == [GROUP]
    assert "reply_markup" in bot.sent[0]
    assert storage.logged == [(None, "pending")]


def test_failed_canned_reply_falls_back_to_a_draft():
    # The category's auto mode is not tried a second time either.
    storage = _Storage(category="payout", auto=["payout"])

    bot = draft(storage, engine(canned_replies=CANNED), bot=_Bot(blocked=True))

    assert bot.blocked_attempts == 1
    assert [m["chat_id"] for m in bot.sent] == [GROUP]
    assert "reply_markup" in bot.sent[0]
    assert storage.logged == [("payout", "pending")]
    assert storage.draft == "Payouts go out on Fridays."


def test_canned_replies_reach_the_prompt_in_the_reply_language():
    provider = _Provider()

    draft(_Storage(), engine(canned_replies=CANNED), provider=provider, language="ru")

    prompt = provider.messages[0]["content"]
    assert "word for word" in prompt
    assert "[payday]\nВсё выплачиваем по пятницам." in prompt
    # No Russian text: the English one stands in.
    assert "[wait]\nPlease wait, a manager will reply soon." in prompt
    assert "Payouts go out on Fridays." not in prompt


def test_no_canned_replies_leave_the_prompt_alone():
    provider = _Provider()

    draft(_Storage(), engine(), provider=provider)

    assert "word for word" not in provider.messages[0]["content"]


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
