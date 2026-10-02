import asyncio
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError

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

    def __init__(self, draft: str | None = "Draft text", message_id: int | None = None) -> None:
        self.draft = draft
        self.message_id = message_id
        self.logged: list[tuple] = []
        self.resolved: list[tuple] = []
        self.conversation: list[tuple] = []

    async def get_conversation(self, user_id, limit):
        return [{"role": "user", "content": "where is my payout?"}]

    async def set_ai_draft(self, user_id, text):
        previous = self.message_id if self.draft is not None else None
        self.draft, self.message_id = text, 0
        return previous

    async def set_ai_draft_message(self, user_id, message_id, text):
        if self.draft == text:
            self.message_id = message_id

    async def get_ai_draft(self, user_id):
        return None if self.draft is None else (self.draft, self.message_id)

    async def clear_ai_draft(self, user_id):
        self.draft = self.message_id = None

    async def append_conversation(self, user_id, role, text):
        self.conversation.append((role, text))

    async def log_draft(self, user_id, thread_id, category, text, outcome="pending"):
        self.logged.append((user_id, thread_id, category, text, outcome))

    async def resolve_draft(self, user_id, outcome):
        self.resolved.append((user_id, outcome))

    async def get_user_category(self, user_id):
        return None

    async def get_by_message_thread_id(self, thread_id):
        return make_user()


class _Bot:
    def __init__(self, error: Exception | None = None, edit_error: Exception | None = None) -> None:
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.error = error
        self.edit_error = edit_error

    async def send_message(self, **kwargs):
        if self.error is not None:
            raise self.error
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=100 + len(self.sent))

    async def edit_message_reply_markup(self, **kwargs):
        if self.edit_error is not None:
            raise self.edit_error
        self.edited.append(kwargs)


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


def draft(storage, policy, provider_reply="Payouts go out on Fridays.", bot=None):
    bot = bot or _Bot()
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
    def __init__(self, message_id: int, text: str | None = None) -> None:
        self.message_id = message_id
        self.text = text
        self.buttons_removed = False
        self.deleted = False

    async def edit_reply_markup(self, reply_markup=None):
        self.buttons_removed = True

    async def delete(self):
        self.deleted = True


class _Call:
    def __init__(
            self, data: str, message_id: int = 101, error: Exception | None = None, text: str | None = None,
    ) -> None:
        self.data = data
        self.bot = _Bot(error)
        self.message = _CallbackMessage(message_id, text)
        self.answers: list = []
        self.alerts: list = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)
        self.alerts.append(show_alert)


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


def press(call: _Call, storage: _Storage) -> _Call:
    asyncio.run(group_callback.ai_draft_callback(call, manager(), storage, engine(log_drafts=True)))
    return call


def test_new_draft_drops_the_buttons_of_the_previous_one():
    storage = _Storage(draft=None)
    bot = _Bot()
    draft(storage, engine(log_drafts=True), "First draft.", bot)
    draft(storage, engine(log_drafts=True), "Second draft.", bot)

    assert storage.draft == "Second draft."
    assert storage.message_id == 102
    assert bot.edited == [{"chat_id": -100, "message_id": 101, "reply_markup": None}]


def test_failing_to_drop_old_buttons_is_only_logged(caplog):
    storage = _Storage(draft="First draft.", message_id=50)
    bot = _Bot(edit_error=TelegramBadRequest(method=None, message="Bad Request: message to edit not found"))
    draft(storage, engine(), "Second draft.", bot)

    assert storage.message_id == 101
    assert "message to edit not found" in caplog.text


@pytest.mark.parametrize("action", ["send", "skip"])
def test_old_draft_buttons_do_nothing(action):
    storage = _Storage(draft="Newest draft", message_id=102)
    call = press(_Call(f"ai:{action}:42", message_id=101), storage)

    assert call.bot.sent == []
    assert call.answers == ["draft_stale {category}{days}{rate}"]
    assert call.message.buttons_removed and not call.message.deleted
    # The newest draft stays pending, and so does its log record.
    assert (storage.draft, storage.message_id) == ("Newest draft", 102)
    assert storage.resolved == []


def test_draft_still_being_posted_is_not_sent_by_old_buttons():
    storage = _Storage(draft="Newest draft", message_id=0)
    call = press(_Call("ai:send:42", message_id=101), storage)
    assert call.bot.sent == []
    assert storage.draft == "Newest draft"


def test_unrecorded_draft_message_is_recorded_on_press():
    # The draft was posted, but storing its message id failed.
    storage = _Storage(draft="Newest draft", message_id=0)
    call = press(_Call("ai:send:42", message_id=101, text="AI draft\n\nNewest draft"), storage)

    assert [m["text"] for m in call.bot.sent] == ["Newest draft"]
    assert storage.resolved == [(42, "sent")]


def test_unrecorded_draft_is_kept_when_another_message_is_pressed():
    storage = _Storage(draft="Newest draft", message_id=0)
    call = press(_Call("ai:skip:42", message_id=100, text="AI draft\n\nOlder draft"), storage)

    assert call.answers == ["draft_stale {category}{days}{rate}"]
    assert (storage.draft, storage.message_id, storage.resolved) == ("Newest draft", 0, [])


def test_failing_to_record_the_draft_message_is_only_logged(caplog):
    class _FailingStorage(_Storage):
        async def set_ai_draft_message(self, user_id, message_id, text):
            raise ConnectionError("connection reset")

    storage = _FailingStorage(draft="First draft.", message_id=50)
    bot = _Bot()
    draft(storage, engine(), "Second draft.", bot)

    assert storage.message_id == 0
    assert bot.edited == [{"chat_id": -100, "message_id": 50, "reply_markup": None}]
    assert "Failed to record the draft message" in caplog.text


@pytest.mark.parametrize("message_id", [None, 101])
def test_current_or_legacy_draft_is_sent(message_id):
    storage = _Storage(draft="Draft text", message_id=message_id)
    call = press(_Call("ai:send:42", message_id=101), storage)

    assert [m["text"] for m in call.bot.sent] == ["Draft text"]
    assert storage.resolved == [(42, "sent")]
    assert storage.draft is None


@pytest.mark.parametrize("error,text", [
    (TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user"), "draft_send_blocked"),
    (TelegramBadRequest(method=None, message="Bad Request: chat not found"), "draft_send_failed"),
    (TelegramNetworkError(method=None, message="network is down"), "draft_send_failed"),
])
def test_failed_send_keeps_the_draft(error, text, caplog):
    storage = _Storage(draft="Draft text", message_id=101)
    call = press(_Call("ai:send:42", message_id=101, error=error), storage)

    assert call.answers == [text + " {category}{days}{rate}"]
    assert call.alerts == [True]
    assert storage.resolved == []
    assert storage.conversation == []
    assert (storage.draft, storage.message_id) == ("Draft text", 101)
    assert not call.message.buttons_removed
    assert "Failed to send the AI draft" in caplog.text


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
