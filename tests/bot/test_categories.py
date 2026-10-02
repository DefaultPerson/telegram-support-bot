import asyncio
from types import SimpleNamespace

import pytest

from app.bot.handlers.private import message as private_message
from app.bot.policy import load_policy_from_dict
from app.bot.policy.schema import AICategory
from app.bot.utils.admins import notify_admins, topic_link
from app.bot.utils.policy_runtime import parse_category, run_ai_layer
from app.bot.utils.redis.models import UserData

CATEGORIES = [
    {"key": "payout", "title": "Payouts", "icon": "💸", "notify_admins": True},
    {"key": "listing", "title": "Channel listing", "icon": "📢"},
    {"key": "other", "title": "Something else"},
]


def categories(*items) -> list[AICategory]:
    return [AICategory(**item) for item in items]


@pytest.mark.parametrize("answer,expected", [
    ("payout", "payout"),
    ("  PAYOUT\n", "payout"),
    ("`payout`", "payout"),
    ('"listing".', "listing"),
    ("Category: payout", "payout"),
    ("This looks like a channel listing request", "listing"),
    ("payout or listing", "other"),
    ("refund", "other"),
    ("", "other"),
    (None, "other"),
])
def test_parse_category(answer, expected):
    assert parse_category(answer, categories(*CATEGORIES)) == expected


def test_unknown_answer_without_other_means_no_category():
    assert parse_category("refund", categories(*CATEGORIES[:2])) is None


def test_category_key_is_short_enough_for_callback_data():
    with pytest.raises(ValueError):
        AICategory(key="k" * 41, title="Too long")


def test_topic_link():
    assert topic_link(-1001234567890, 7) == "https://t.me/c/1234567890/7"


class _Bot:
    def __init__(self, unreachable=()) -> None:
        self.sent: list[dict] = []
        self.topics: list[dict] = []
        self.unreachable = set(unreachable)

    async def send_message(self, **kwargs):
        if kwargs["chat_id"] in self.unreachable:
            raise RuntimeError("chat not found")
        self.sent.append(kwargs)

    async def edit_forum_topic(self, **kwargs):
        self.topics.append(kwargs)


def config(admins=(1, 2)):
    from app.config import AIConfig

    ai = AIConfig(
        PROVIDER="openai_compatible", BASE_URL="", API_KEY="k", MODEL="m",
        SYSTEM_PROMPT_PATH="", TIMEOUT_S=5, VISION=False,
    )
    return SimpleNamespace(ai=ai, bot=SimpleNamespace(GROUP_ID=-1001234567890, DEV_IDS=list(admins)))


def test_every_admin_is_notified_even_if_one_is_unreachable():
    bot = _Bot(unreachable={1})
    asyncio.run(notify_admins(bot, config(admins=(1, 2, 3)), "hello"))
    assert [m["chat_id"] for m in bot.sent] == [2, 3]


class _Storage:
    def __init__(self) -> None:
        self.category = None
        self.logged: list[tuple] = []

    async def set_user_category(self, user_id, category):
        self.category = category

    async def get_user_category(self, user_id):
        return self.category

    async def get_conversation(self, user_id, limit):
        return [{"role": "user", "content": "my payout is late"}]

    async def set_ai_draft(self, user_id, text):
        return None

    async def log_draft(self, user_id, thread_id, category, text, outcome="pending"):
        self.logged.append((category, outcome))

    async def get_auto_mode(self, category):
        return False


class _Provider:
    """Answers the classification request with `category`, the draft request with a reply."""

    def __init__(self, category: str) -> None:
        self.category = category
        self.requests: list[list] = []

    async def draft_reply(self, messages):
        self.requests.append(messages)
        if "category key only" in messages[0]["content"]:
            return self.category
        return "Payouts go out on Fridays."


def make_user() -> UserData:
    return UserData(
        message_thread_id=7, message_silent_id=None, message_silent_mode=False,
        id=42, full_name="Jane Doe", username="-", language_code="en",
    )


def layer(answer, policy, classify=True, draft=True):
    bot = _Bot()
    storage = _Storage()
    provider = _Provider(answer)
    message = SimpleNamespace(bot=bot, text="my payout is late", caption=None)
    asyncio.run(run_ai_layer(
        provider, config(), message, storage, make_user(), policy.ai, classify=classify, draft=draft,
    ))
    return SimpleNamespace(bot=bot, storage=storage, provider=provider)


def test_first_message_category_is_applied_everywhere():
    policy = load_policy_from_dict({"ai": {"log_drafts": True, "categories": CATEGORIES}})

    run = layer("payout", policy)

    assert run.storage.category == "payout"
    # The prompt lists every key.
    assert "- listing: Channel listing" in run.provider.requests[0][0]["content"]
    # The icon goes before the original topic name.
    assert run.bot.topics == [{"chat_id": -1001234567890, "message_thread_id": 7, "name": "💸 Jane Doe"}]
    # Both admins get a link to the topic.
    notices = [m for m in run.bot.sent if m["chat_id"] in (1, 2)]
    assert [m["chat_id"] for m in notices] == [1, 2]
    assert "https://t.me/c/1234567890/7" in notices[0]["text"]
    # The draft names the category and the log records it.
    draft = next(m for m in run.bot.sent if m["chat_id"] == -1001234567890)
    assert "💸 Payouts" in draft["text"]
    assert run.storage.logged == [("payout", "pending")]


def test_category_without_icon_or_notice_leaves_topic_and_admins_alone():
    policy = load_policy_from_dict({"ai": {"categories": CATEGORIES}})

    run = layer("something odd", policy)

    assert run.storage.category == "other"
    assert run.bot.topics == []
    assert all(m["chat_id"] == -1001234567890 for m in run.bot.sent)


def test_classification_failure_keeps_the_draft():
    class Failing(_Provider):
        async def draft_reply(self, messages):
            if "category key only" in messages[0]["content"]:
                raise RuntimeError("boom")
            return "Payouts go out on Fridays."

    policy = load_policy_from_dict({"ai": {"categories": CATEGORIES}})
    bot = _Bot()
    storage = _Storage()
    message = SimpleNamespace(bot=bot, text="hi", caption=None)

    asyncio.run(run_ai_layer(Failing(""), config(), message, storage, make_user(), policy.ai, classify=True))

    assert storage.category is None
    assert len(bot.sent) == 1
    assert "Category" not in bot.sent[0]["text"]


def test_classification_runs_even_when_the_draft_is_suppressed():
    policy = load_policy_from_dict({"ai": {"categories": CATEGORIES}})

    run = layer("listing", policy, draft=False)

    assert run.storage.category == "listing"
    assert len(run.provider.requests) == 1


class _HandlerStorage:
    def __init__(self) -> None:
        self.written: set[int] = set()

    async def append_conversation(self, user_id, role, text):
        return None

    async def claim_first_message(self, user_id):
        first = user_id not in self.written
        self.written.add(user_id)
        return first


@pytest.fixture()
def handler(monkeypatch):
    """Drive the private handler and record what the AI layer is asked to do."""
    calls = []

    async def fake_topic(bot, redis, config, user_data):
        return 7

    async def no_sleep(_):
        return None

    def fake_layer(*args, classify, draft):
        calls.append({"classify": classify, "draft": draft})

    def create_task(value):
        return None

    monkeypatch.setattr(private_message, "get_or_create_forum_topic", fake_topic)
    monkeypatch.setattr(private_message, "run_ai_layer", fake_layer)
    monkeypatch.setattr(
        private_message, "asyncio", SimpleNamespace(sleep=no_sleep, create_task=create_task)
    )

    storage = _HandlerStorage()
    manager = SimpleNamespace(
        config=SimpleNamespace(bot=SimpleNamespace(GROUP_ID=-100)),
        text_message=SimpleNamespace(get=lambda key: key),
    )

    def send(policy):
        async def reply(text):
            return SimpleNamespace(delete=no_sleep_delete)

        async def no_sleep_delete():
            return None

        async def forward(**kwargs):
            return None

        message = SimpleNamespace(bot=_Bot(), text="hello", caption=None, reply=reply, forward=forward)
        asyncio.run(private_message.handle_incoming_message(
            message, manager, storage, make_user(), policy_engine=policy, llm_provider=object(),
        ))

    return SimpleNamespace(send=send, calls=calls)


def test_only_the_first_message_is_classified(handler):
    policy = load_policy_from_dict({"ai": {"categories": CATEGORIES}})
    handler.send(policy)
    handler.send(policy)
    assert handler.calls == [{"classify": True, "draft": True}, {"classify": False, "draft": True}]


def test_no_categories_means_no_classification(handler):
    handler.send(load_policy_from_dict({}))
    handler.send(None)
    assert [c["classify"] for c in handler.calls] == [False, False]
