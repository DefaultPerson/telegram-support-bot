import asyncio
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from app.bot.handlers.group import ai as group_ai
from app.bot.handlers.group import callback_query as group_callback
from app.bot.handlers.group import message as group_message
from app.bot.policy import load_policy_from_dict
from app.bot.utils.policy_runtime import run_ai_draft
from app.bot.utils.redis.models import UserData

ADMIN = 1


def engine(**ai):
    return load_policy_from_dict({"ai": ai})


def make_user(silent: bool = False) -> UserData:
    return UserData(
        message_thread_id=7, message_silent_id=None, message_silent_mode=silent,
        id=42, full_name="User", username="-", language_code="en",
    )


class _Storage:
    """In-memory stand-in for the draft-related part of RedisStorage."""

    def __init__(self, draft: str | None = "Draft text", stats=None) -> None:
        self.draft = draft
        self.logged: list[tuple] = []
        self.resolved: list[tuple] = []
        self.conversation: list[tuple] = []
        self.stats = stats or {}
        self.stats_days = "unset"

    async def get_conversation(self, user_id, limit):
        return [{"role": "user", "content": "where is my payout?"}]

    async def set_ai_draft(self, user_id, text):
        self.draft = text

    async def get_ai_draft(self, user_id):
        return self.draft

    async def clear_ai_draft(self, user_id):
        self.draft = None

    async def append_conversation(self, user_id, role, text):
        self.conversation.append((role, text))

    async def log_draft(self, user_id, thread_id, category, text, outcome="pending"):
        self.logged.append((user_id, thread_id, category, text, outcome))

    async def resolve_draft(self, user_id, outcome):
        self.resolved.append((user_id, outcome))

    async def get_user_category(self, user_id):
        return None

    async def get_draft_stats(self, days=None):
        self.stats_days = days
        return self.stats

    async def get_by_message_thread_id(self, thread_id):
        return make_user()


class _Bot:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class _Provider:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def draft_reply(self, messages):
        return self.reply


def ai_config():
    from app.config import AIConfig

    return AIConfig(
        PROVIDER="openai_compatible", BASE_URL="", API_KEY="k", MODEL="m",
        SYSTEM_PROMPT_PATH="", TIMEOUT_S=5, VISION=False,
    )


def draft(storage, policy, provider_reply="Payouts go out on Fridays."):
    bot = _Bot()
    config = SimpleNamespace(ai=ai_config(), bot=SimpleNamespace(GROUP_ID=-100, DEV_IDS=[ADMIN]))
    message = SimpleNamespace(bot=bot, text="where is my payout?", caption=None)
    asyncio.run(run_ai_draft(
        _Provider(provider_reply), config, message, storage, make_user(), 12, ai=policy.ai,
    ))
    return bot


def test_draft_is_logged_as_pending_when_enabled():
    storage = _Storage()
    bot = draft(storage, engine(log_drafts=True))

    assert storage.logged == [(42, 7, None, "Payouts go out on Fridays.", "pending")]
    assert "reply_markup" in bot.sent[0]


def test_draft_is_not_logged_by_default():
    storage = _Storage()
    draft(storage, engine())
    assert storage.logged == []


def manager(admin_id=ADMIN):
    return SimpleNamespace(
        config=SimpleNamespace(bot=SimpleNamespace(GROUP_ID=-100, DEV_IDS=[admin_id])),
        text_message=SimpleNamespace(get=lambda key: key + " {category}{days}{rate}"),
    )


class _CallbackMessage:
    async def edit_reply_markup(self, reply_markup=None):
        return None

    async def delete(self):
        return None


class _Call:
    def __init__(self, data: str) -> None:
        self.data = data
        self.bot = _Bot()
        self.message = _CallbackMessage()
        self.answers: list = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)


@pytest.mark.parametrize("action,outcome", [("send", "sent"), ("skip", "skipped")])
def test_buttons_resolve_the_pending_draft(action, outcome):
    storage = _Storage()
    call = _Call(f"ai:{action}:42")

    asyncio.run(group_callback.ai_draft_callback(call, manager(), storage, engine(log_drafts=True)))

    assert storage.resolved == [(42, outcome)]


def test_buttons_leave_the_log_alone_by_default():
    storage = _Storage()
    asyncio.run(group_callback.ai_draft_callback(_Call("ai:send:42"), manager(), storage))
    assert storage.resolved == []


def test_expired_draft_is_not_marked_sent():
    storage = _Storage(draft=None)
    asyncio.run(group_callback.ai_draft_callback(
        _Call("ai:send:42"), manager(), storage, engine(log_drafts=True)
    ))
    assert storage.resolved == []


class _TopicMessage:
    def __init__(self, text: str, error: Exception | None = None) -> None:
        self.text = text
        self.caption = None
        self.message_thread_id = 7
        self.error = error

    async def copy_to(self, chat_id):
        if self.error is not None:
            raise self.error

    async def reply(self, text):
        async def delete():
            return None

        return SimpleNamespace(delete=delete)


@pytest.fixture()
def manager_reply(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(group_message, "asyncio", SimpleNamespace(sleep=no_sleep))
    # The last handler of the module is the one that forwards manager messages.
    handler = group_message.router.message.handlers[-1].callback

    def send(text, policy, storage=None, error=None):
        storage = storage or _Storage()
        asyncio.run(handler(_TopicMessage(text, error), manager(), storage, None, policy))
        return storage

    return send


def test_manager_reply_marks_the_draft(manager_reply):
    storage = manager_reply("Hi, payouts go out on Fridays.", engine(log_drafts=True))
    assert storage.resolved == [(42, "manager_replied")]


@pytest.mark.parametrize("error", [
    TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user"),
    TelegramBadRequest(method=None, message="Bad Request: chat not found"),
    RuntimeError("network is down"),
])
def test_undelivered_reply_leaves_the_draft_pending(manager_reply, error):
    storage = manager_reply("Hi, payouts go out on Fridays.", engine(log_drafts=True), error=error)
    assert storage.resolved == []


def test_command_in_topic_is_not_a_reply(manager_reply):
    storage = manager_reply("/whatever", engine(log_drafts=True))
    assert storage.resolved == []


def test_manager_reply_leaves_the_log_alone_by_default(manager_reply):
    assert manager_reply("Hi", engine()).resolved == []
    assert manager_reply("Hi", None).resolved == []


def test_silent_mode_reply_is_not_counted(manager_reply):
    storage = _Storage()

    async def silent(thread_id):
        return make_user(silent=True)

    storage.get_by_message_thread_id = silent
    manager_reply("note for colleagues", engine(log_drafts=True), storage)
    assert storage.resolved == []


class _CommandMessage:
    def __init__(self, user_id: int) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.replies: list[str] = []

    async def reply(self, text, reply_markup=None):
        self.replies.append(text)


def stats_command(args, policy, storage=None, user_id=ADMIN):
    storage = storage or _Storage()
    message = _CommandMessage(user_id)
    command = SimpleNamespace(args=args)
    mgr = SimpleNamespace(
        config=SimpleNamespace(bot=SimpleNamespace(DEV_IDS=[ADMIN])),
        text_message=SimpleNamespace(
            get=lambda key: {
                "ai_stats_row": "{category}: {total} {sent} {skipped} {manager_replied} {auto_sent} {rate}",
                "ai_stats_header_days": "days {days}",
                "no_category": "none",
            }.get(key, key),
        ),
    )
    asyncio.run(group_ai.ai_stats_handler(message, command, mgr, storage, policy))
    return message.replies, storage


def row(total, sent, skipped, replied, auto=0):
    return {"total": total, "sent": sent, "skipped": skipped, "manager_replied": replied, "auto_sent": auto}


def test_ai_stats_is_admin_only():
    replies, _ = stats_command("", engine(log_drafts=True), user_id=999)
    assert replies == ["ai_admins_only"]


def test_ai_stats_needs_the_log():
    replies, _ = stats_command("", engine())
    assert replies == ["ai_stats_disabled"]


@pytest.mark.parametrize("args", ["week", "0", "-7", "3651", "99999999999", "²"])
def test_ai_stats_rejects_a_bad_period(args):
    replies, storage = stats_command(args, engine(log_drafts=True))
    assert replies == ["ai_stats_usage"]
    assert storage.stats_days == "unset"


def test_ai_stats_accepts_the_longest_period():
    _, storage = stats_command("3650", engine(log_drafts=True))
    assert storage.stats_days == 3650


def test_ai_stats_counts_per_category():
    policy = engine(log_drafts=True, categories=[
        {"key": "payout", "title": "Payouts", "icon": "💸"},
        {"key": "other", "title": "Other"},
    ])
    storage = _Storage(stats={
        None: row(2, 0, 1, 0),
        "other": row(3, 1, 1, 0),
        "payout": row(10, 6, 1, 1, auto=2),
    })

    replies, storage = stats_command("7", policy, storage)

    assert storage.stats_days == 7
    lines = replies[0].split("\n")
    assert lines[0] == "days 7"
    # Config order first, uncategorised last; the share counts reviewed drafts only.
    assert lines[1] == "💸 Payouts: 10 6 1 1 2 75%"
    assert lines[2] == "Other: 3 1 1 0 0 50%"
    assert lines[3] == "none: 2 0 1 0 0 0%"
