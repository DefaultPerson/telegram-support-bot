import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from pydantic import ValidationError

import app.__main__ as bot_main
from app.bot.handlers.group import callback_query as group_callback
from app.bot.handlers.group import message as group_message
from app.bot.handlers.private import message as private_message
from app.bot.policy import load_policy_from_dict
from app.bot.utils import reminders
from app.bot.utils.policy_runtime import run_ai_draft
from app.bot.utils.redis.models import UserData

GROUP = -100
CATEGORIES = [{"key": "payout", "title": "Payouts"}, {"key": "spam", "title": "Spam"}]


def engine(enabled=True, rules=(), **section):
    return load_policy_from_dict({
        "ai": {"categories": CATEGORIES},
        "reminders": {"enabled": enabled, **section},
        "rules": list(rules),
    })


def make_user(silent=False, banned=False) -> UserData:
    return UserData(
        message_thread_id=7, message_silent_id=None, message_silent_mode=silent,
        id=42, full_name="User", username="-", language_code="en", is_banned=banned,
    )


class _Storage:
    """In-memory stand-in for RedisStorage, recording reply waits."""

    def __init__(self, due=(), claim=True) -> None:
        self.waits: list[tuple] = []
        self.due = list(due)
        self.claim = claim
        self.claims: list[tuple] = []
        self.releases: list[tuple] = []
        self.draft = "Draft text"
        self.user = make_user()

    async def start_reply_wait(self, user_id):
        self.waits.append(("start", user_id))

    async def end_reply_wait(self, user_id):
        self.waits.append(("end", user_id))

    async def get_due_reply_waits(self, after_minutes, skip_categories):
        self.query = (after_minutes, skip_categories)
        return self.due

    async def claim_reply_reminder(self, user_id, since, level):
        self.claims.append((user_id, level))
        return self.claim

    async def release_reply_reminder(self, user_id, since, level, reminded):
        self.releases.append((user_id, level, reminded))

    async def append_conversation(self, user_id, role, text):
        return None

    async def claim_first_message(self, user_id):
        return False

    async def release_first_message(self, user_id):
        return None

    async def claim_auto_reply(self, user_id, template_key):
        return True

    async def update_user(self, id_, data):
        return None

    async def get_by_message_thread_id(self, thread_id):
        return self.user

    async def get_ai_draft(self, user_id):
        return self.draft

    async def set_ai_draft(self, user_id, text):
        self.draft = text

    async def clear_ai_draft(self, user_id):
        self.draft = None

    async def get_conversation(self, user_id, limit):
        return [{"role": "user", "content": "where is my payout?"}]

    async def get_user_category(self, user_id):
        return "payout"

    async def get_auto_mode(self, category):
        return True


class _Bot:
    def __init__(self, fail_for=(), errors=()) -> None:
        self.sent: list[dict] = []
        self.fail_for = set(fail_for)
        # Raised by the next sends, one each, before they succeed again.
        self.errors = list(errors)

    async def send_message(self, **kwargs):
        if kwargs.get("message_thread_id") in self.fail_for:
            raise TelegramBadRequest(method=None, message="Bad Request: message thread not found")
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append(kwargs)


def test_reminders_are_off_by_default():
    section = load_policy_from_dict({}).reminders
    assert section.enabled is False
    assert section.after_minutes == [180, 1440]
    assert section.check_interval_minutes == 10
    assert reminders.reminders_enabled(None) is False
    assert reminders.reminders_enabled(load_policy_from_dict({})) is False


def test_thresholds_are_sorted():
    assert engine(after_minutes=[1440, 180, 180]).reminders.after_minutes == [180, 1440]


@pytest.mark.parametrize("section", [
    {"after_minutes": [0]},
    {"check_interval_minutes": 0},
    {"skip_categories": ["unknown"]},
    {"typo": True},
])
def test_bad_reminders_section_is_rejected(section):
    with pytest.raises(ValidationError):
        engine(**section)


@pytest.mark.parametrize("minutes,hours", [(1, 1), (89, 1), (90, 2), (180, 3), (1440, 24), (1475, 25)])
def test_waited_hours_are_rounded(minutes, hours):
    assert reminders.waited_hours(minutes) == hours


class _Message:
    def __init__(self, bot, text="my payout is late") -> None:
        self.bot = bot
        self.text = text
        self.caption = None

    async def answer(self, text):
        return None

    async def forward(self, **kwargs):
        return None

    async def reply(self, text):
        async def delete():
            return None

        return SimpleNamespace(delete=delete)


@pytest.fixture()
def user_writes(monkeypatch):
    """Drive the private message handler; returns the storage and the AI layer kwargs."""
    layers = []

    async def fake_topic(bot, redis, config, user_data):
        return 7

    async def no_sleep(_):
        return None

    def fake_layer(*args, **kwargs):
        layers.append(kwargs)

    monkeypatch.setattr(private_message, "get_or_create_forum_topic", fake_topic)
    monkeypatch.setattr(private_message, "run_ai_layer", fake_layer)
    monkeypatch.setattr(
        private_message, "asyncio", SimpleNamespace(sleep=no_sleep, create_task=lambda value: None)
    )
    manager = SimpleNamespace(
        config=SimpleNamespace(bot=SimpleNamespace(GROUP_ID=GROUP, DEV_ID=1)),
        text_message=SimpleNamespace(get=lambda key: key),
    )

    def send(policy, user=None):
        storage = _Storage()
        asyncio.run(private_message.handle_incoming_message(
            _Message(_Bot()), manager, storage, user or make_user(),
            policy_engine=policy, llm_provider=object(),
        ))
        return storage, layers[-1] if layers else None

    return send


def test_user_message_starts_the_wait(user_writes):
    storage, layer = user_writes(engine())
    assert storage.waits == [("start", 42)]
    # The AI layer may answer automatically and end the wait.
    assert layer["reminders"] is True


@pytest.mark.parametrize("policy", [None, engine(enabled=False)])
def test_no_wait_without_reminders(user_writes, policy):
    storage, layer = user_writes(policy)
    assert storage.waits == []
    assert layer["reminders"] is False


def test_banned_user_starts_no_wait(user_writes):
    storage, _ = user_writes(engine(), make_user(banned=True))
    assert storage.waits == []


def test_message_kept_out_of_the_topic_starts_no_wait(user_writes):
    policy = engine(rules=[{
        "id": "drop", "when": {"event_type": "user_message"},
        "actions": [{"type": "suppress_topic_creation"}],
    }])
    storage, _ = user_writes(policy)
    assert storage.waits == []


class _TopicMessage:
    def __init__(self, text, error=None) -> None:
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
def manager_writes(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(group_message, "asyncio", SimpleNamespace(sleep=no_sleep))
    handler = group_message.router.message.handlers[-1].callback
    manager = SimpleNamespace(text_message=SimpleNamespace(get=lambda key: key))

    def send(text, policy, error=None, silent=False):
        storage = _Storage()
        storage.user = make_user(silent=silent)
        asyncio.run(handler(_TopicMessage(text, error), manager, storage, None, policy))
        return storage.waits

    return send


def test_manager_reply_ends_the_wait(manager_writes):
    assert manager_writes("Payouts go out on Fridays.", engine()) == [("end", 42)]


@pytest.mark.parametrize("text,policy,error,silent", [
    ("/whatever", engine(), None, False),
    ("Hi", engine(), RuntimeError("network is down"), False),
    ("note for colleagues", engine(), None, True),
    ("Hi", engine(enabled=False), None, False),
    ("Hi", None, None, False),
])
def test_other_topic_messages_leave_the_wait(manager_writes, text, policy, error, silent):
    assert manager_writes(text, policy, error, silent) == []


class _Call:
    def __init__(self, data) -> None:
        self.data = data
        self.bot = _Bot()
        self.message = SimpleNamespace(edit_reply_markup=self._noop, delete=self._noop)

    @staticmethod
    async def _noop(*args, **kwargs):
        return None

    async def answer(self, text=None, show_alert=False):
        return None


@pytest.mark.parametrize("data,waits", [
    ("ai:send:42", [("end", 42)]),
    ("ai:skip:42", []),
])
def test_sent_draft_ends_the_wait(data, waits):
    storage = _Storage()
    manager = SimpleNamespace(text_message=SimpleNamespace(get=lambda key: key))
    asyncio.run(group_callback.ai_draft_callback(_Call(data), manager, storage, engine()))
    assert storage.waits == waits


def test_sent_draft_leaves_the_wait_without_reminders():
    storage = _Storage()
    manager = SimpleNamespace(text_message=SimpleNamespace(get=lambda key: key))
    asyncio.run(group_callback.ai_draft_callback(_Call("ai:send:42"), manager, storage, None))
    assert storage.waits == []


class _Provider:
    async def draft_reply(self, messages):
        return "Payouts go out on Fridays."


@pytest.mark.parametrize("flag", [True, False])
def test_automatic_reply_ends_the_wait(flag):
    from app.config import AIConfig

    storage = _Storage()
    ai = AIConfig(
        PROVIDER="openai_compatible", BASE_URL="", API_KEY="k", MODEL="m",
        SYSTEM_PROMPT_PATH="", TIMEOUT_S=5, VISION=False,
    )
    config = SimpleNamespace(ai=ai, bot=SimpleNamespace(GROUP_ID=GROUP))
    message = SimpleNamespace(bot=_Bot(), text="where is my payout?", caption=None)
    asyncio.run(run_ai_draft(
        _Provider(), config, message, storage, make_user(), 12, ai=engine().ai, reminders=flag,
    ))
    assert storage.waits == ([("end", 42)] if flag else [])


def wait(user_id, thread_id, minutes, level, language="en"):
    return {
        "user_id": user_id, "since": datetime(2026, 1, 1, tzinfo=timezone.utc), "reminded": level - 1,
        "message_thread_id": thread_id, "language_code": language,
        "waited_minutes": minutes, "level": level,
    }


def check(storage, bot, policy):
    config = SimpleNamespace(bot=SimpleNamespace(GROUP_ID=GROUP))
    asyncio.run(reminders.check_reply_waits(bot, config, storage, policy.reminders))


def test_due_waits_are_reminded_in_their_topic():
    storage = _Storage(due=[wait(42, 7, 185, 1), wait(43, 8, 1500, 2, language="ru")])
    bot = _Bot()

    check(storage, bot, engine(skip_categories=["spam"]))

    assert storage.query == ([180, 1440], ["spam"])
    assert storage.claims == [(42, 1), (43, 2)]
    assert bot.sent == [
        {"chat_id": GROUP, "message_thread_id": 7,
         "text": "⏰ The user has been waiting for a reply for 3 h"},
        {"chat_id": GROUP, "message_thread_id": 8, "text": "⏰ Клиент ждёт ответа 25 ч"},
    ]


def test_claimed_reminder_is_not_repeated():
    storage = _Storage(due=[wait(42, 7, 185, 1)], claim=False)
    bot = _Bot()
    check(storage, bot, engine())
    assert bot.sent == []


def test_failed_reminder_is_logged_and_the_rest_go_out(caplog):
    storage = _Storage(due=[wait(42, 7, 185, 1), wait(43, 8, 185, 1)])
    bot = _Bot(fail_for=[7])

    with caplog.at_level(logging.WARNING):
        check(storage, bot, engine())

    assert [m["message_thread_id"] for m in bot.sent] == [8]
    assert "user 42" in caplog.text
    # A topic that is gone will not come back: the reminder is not retried.
    assert storage.releases == []


def flood(seconds=7):
    return TelegramRetryAfter(method=SimpleNamespace(), message="Too Many Requests", retry_after=seconds)


@pytest.fixture()
def sleeps(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(reminders.asyncio, "sleep", fake_sleep)
    return slept


def test_flood_limit_is_waited_out(sleeps):
    storage = _Storage(due=[wait(42, 7, 185, 1), wait(43, 8, 185, 1)])
    bot = _Bot(errors=[flood(7)])

    check(storage, bot, engine())

    assert sleeps == [7]
    assert [m["message_thread_id"] for m in bot.sent] == [7, 8]
    assert storage.releases == []


@pytest.mark.parametrize("errors", [
    [flood()] * reminders.SEND_ATTEMPTS,
    [TelegramNetworkError(method=None, message="timeout")],
])
def test_temporary_failure_leaves_the_reminder_to_the_next_check(sleeps, errors):
    storage = _Storage(due=[wait(42, 7, 185, 2), wait(43, 8, 185, 1)])
    bot = _Bot(errors=errors)

    check(storage, bot, engine())

    assert storage.releases == [(42, 2, 1)]
    assert [m["message_thread_id"] for m in bot.sent] == [8]


@pytest.mark.parametrize("policy,running", [
    (None, False), (engine(enabled=False), False), (engine(), True),
])
def test_startup_drops_the_waits_while_reminders_are_off(monkeypatch, policy, running):
    calls = []

    async def fake_drop(pool):
        calls.append("drop")

    async def fake_run(*args):
        calls.append("run")

    async def fake_setup(bot, config):
        return None

    monkeypatch.setattr(bot_main, "drop_reply_waits", fake_drop)
    monkeypatch.setattr(bot_main, "run_reply_reminders", fake_run)
    monkeypatch.setattr(bot_main.commands, "setup", fake_setup)
    dispatcher = {}

    async def start():
        await bot_main.on_startup(
            SimpleNamespace(start=lambda: None), dispatcher, None, None, object(), policy
        )
        task = dispatcher.get("reply_reminders_task")
        if task is not None:
            await task

    asyncio.run(start())

    assert calls == (["run"] if running else ["drop"])


def test_dropping_the_waits_never_blocks_the_startup(monkeypatch, caplog):
    class _Failing:
        def __init__(self, pool):
            pass

        async def clear_reply_waits(self):
            raise OSError("database is down")

    monkeypatch.setattr(reminders, "RedisStorage", _Failing)
    with caplog.at_level(logging.WARNING):
        asyncio.run(reminders.drop_reply_waits(object()))
    assert "database is down" in caplog.text
