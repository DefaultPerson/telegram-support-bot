import asyncio
from types import SimpleNamespace

import pytest

from app.bot.handlers.private import message as private_message
from app.bot.policy import load_policy_from_dict
from app.bot.utils.redis.models import UserData

POLICY = {
    "templates": {
        "received": {"en": "Got it, we will answer as soon as we can."},
        "rules": {"en": "Please read the rules."},
    },
    "rules": [
        {"id": "notice", "when": {"event_type": "user_message", "first_message": True},
         "actions": [{"type": "auto_reply", "template_key": "received", "suppress_draft": False}]},
        {"id": "price", "when": {"keywords_any": ["price"]},
         "actions": [{"type": "auto_reply", "template_key": "rules", "once": True}]},
        {"id": "help", "when": {"keywords_any": ["help"]},
         "actions": [{"type": "auto_reply", "template_key": "rules"}]},
    ],
}


class _Storage:
    """In-memory stand-in for the Postgres-backed RedisStorage."""

    def __init__(self) -> None:
        self.conversation: list[tuple[str, str]] = []
        self.written: set[int] = set()
        self.sent_once: set[tuple[int, str]] = set()

    async def append_conversation(self, user_id, role, text):
        self.conversation.append((role, text))

    async def claim_first_message(self, user_id):
        if user_id in self.written:
            return False
        self.written.add(user_id)
        return True

    async def claim_auto_reply(self, user_id, template_key):
        if (user_id, template_key) in self.sent_once:
            return False
        self.sent_once.add((user_id, template_key))
        return True

    async def update_user(self, id_, data):
        return None


class _Bot:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class _Message:
    def __init__(self, bot: _Bot, text: str) -> None:
        self.bot = bot
        self.text = text
        self.caption = None
        self.answers: list[str] = []

    async def answer(self, text):
        self.answers.append(text)

    async def forward(self, **kwargs):
        return None

    async def reply(self, text):
        async def delete():
            return None

        return SimpleNamespace(delete=delete)


@pytest.fixture()
def conversation(monkeypatch):
    """Drive the private message handler; returns a send(text) helper."""
    drafts = []

    async def fake_topic(bot, redis, config, user_data):
        return user_data.message_thread_id

    async def no_sleep(_):
        return None

    def fake_create_task(coro):
        drafts.append(coro)
        coro.close()

    monkeypatch.setattr(private_message, "get_or_create_forum_topic", fake_topic)
    monkeypatch.setattr(
        private_message, "asyncio", SimpleNamespace(sleep=no_sleep, create_task=fake_create_task)
    )

    bot = _Bot()
    storage = _Storage()
    engine = load_policy_from_dict(POLICY)
    manager = SimpleNamespace(
        config=SimpleNamespace(bot=SimpleNamespace(GROUP_ID=-100, DEV_ID=1)),
        text_message=SimpleNamespace(get=lambda key: key),
    )
    user = UserData(
        message_thread_id=7, message_silent_id=None, message_silent_mode=False,
        id=42, full_name="User", username="-", language_code="en",
    )

    def send(text: str):
        message = _Message(bot, text)
        before = len(drafts)
        asyncio.run(private_message.handle_incoming_message(
            message, manager, storage, user, policy_engine=engine, llm_provider=object(),
        ))
        return message.answers, len(drafts) > before

    return SimpleNamespace(send=send, storage=storage, bot=bot)


def test_first_message_notice_is_sent_once_and_keeps_the_draft(conversation):
    answers, drafted = conversation.send("my withdrawal is stuck")
    assert answers == ["Got it, we will answer as soon as we can."]
    assert drafted is True
    # The notice is mirrored to the topic but kept out of the LLM transcript.
    assert any("Got it" in m["text"] for m in conversation.bot.sent)
    assert conversation.storage.conversation == [("user", "my withdrawal is stuck")]

    answers, drafted = conversation.send("any news?")
    assert answers == []
    assert drafted is True


def test_once_reply_is_not_repeated_and_still_suppresses_the_draft(conversation):
    conversation.send("hello")

    answers, drafted = conversation.send("what is the price")
    assert answers == ["Please read the rules."]
    assert drafted is False

    answers, drafted = conversation.send("price again?")
    assert answers == []
    assert drafted is True


def test_reply_without_once_repeats_as_before(conversation):
    conversation.send("hello")

    for _ in range(2):
        answers, drafted = conversation.send("help")
        assert answers == ["Please read the rules."]
        assert drafted is False
