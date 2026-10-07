import asyncio
import logging
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from pydantic import ValidationError

from app.bot.handlers.private import message as private_message
from app.bot.policy import load_policy_from_dict
from app.bot.utils import policy_runtime
from app.bot.utils.redis.models import UserData

GROUP = -1001234567890
CRITERIA = "- the user paid and got nothing"


def engine(**urgent):
    return load_policy_from_dict({"ai": {"urgent": {"enabled": True, "criteria": CRITERIA, **urgent}}})


def make_user() -> UserData:
    return UserData(
        message_thread_id=7, message_silent_id=None, message_silent_mode=False,
        id=42, full_name="User <42>", username="-", language_code="ru",
    )


class _Provider:
    def __init__(self, answer="yes", error=None) -> None:
        self.answer = answer
        self.error = error
        self.requests: list[list[dict]] = []

    async def draft_reply(self, messages):
        self.requests.append(messages)
        if self.error is not None:
            raise self.error
        return self.answer


class _Bot:
    def __init__(self, unreachable=()) -> None:
        self.sent: list[dict] = []
        self.unreachable = set(unreachable)

    async def send_message(self, **kwargs):
        if kwargs["chat_id"] in self.unreachable:
            raise TelegramBadRequest(method=None, message="Bad Request: chat not found")
        self.sent.append(kwargs)


class _Storage:
    def __init__(self, claim=True) -> None:
        self.claim = claim
        self.claims: list[int] = []

    async def claim_urgent_notice(self, user_id):
        self.claims.append(user_id)
        if isinstance(self.claim, Exception):
            raise self.claim
        return self.claim


def check(text, provider=None, storage=None, bot=None, topic_message_id=900):
    provider = provider or _Provider()
    storage = storage or _Storage()
    bot = bot or _Bot()
    config = SimpleNamespace(ai=SimpleNamespace(total_timeout_s=5), bot=SimpleNamespace(GROUP_ID=GROUP, DEV_IDS=[1, 2]))
    message = SimpleNamespace(bot=bot, text=text, caption=None)
    asyncio.run(policy_runtime.run_urgent_check(
        provider, config, message, storage, make_user(), engine().ai, topic_message_id,
    ))
    return bot, storage, provider


def test_urgency_check_is_off_by_default():
    assert load_policy_from_dict({}).ai.urgent.enabled is False


def test_urgency_check_needs_criteria():
    with pytest.raises(ValidationError):
        engine(criteria="  ")


@pytest.mark.parametrize("answer,urgent", [
    ("yes", True), ("Yes.", True), (" YES\n", True), ("Да", True),
    ("no", False), ("Нет", False), ("", False), (None, False), ("maybe yes", False),
])
def test_answer_is_parsed(answer, urgent):
    assert policy_runtime.parse_urgent(answer) is urgent


def test_urgent_message_reaches_every_admin_with_a_link_to_it():
    bot, storage, provider = check("I paid <b>twice</b> and got nothing!")

    link = "https://t.me/c/1234567890/7/900"
    text = (
        f'🚨 Срочное сообщение от <a href="{link}">User &lt;42&gt;</a>:\n'
        f"I paid &lt;b&gt;twice&lt;/b&gt; and got nothing!\n{link}"
    )
    assert bot.sent == [{"chat_id": 1, "text": text}, {"chat_id": 2, "text": text}]
    assert storage.claims == [42]
    (system, user), = provider.requests
    assert CRITERIA in system["content"]
    assert user == {"role": "user", "content": "I paid <b>twice</b> and got nothing!"}


def test_long_message_is_cut_for_the_admins():
    bot, _, _ = check("x" * 400)
    assert "x" * 300 + "…\n" in bot.sent[0]["text"]
    assert "x" * 301 not in bot.sent[0]["text"]


def test_without_the_topic_message_the_link_points_to_the_topic():
    bot, _, _ = check("urgent!", topic_message_id=None)
    assert bot.sent[0]["text"].endswith("\nhttps://t.me/c/1234567890/7")


@pytest.mark.parametrize("provider", [_Provider("no"), _Provider(error=RuntimeError("429"))])
def test_nothing_goes_out_unless_the_model_says_urgent(provider):
    bot, storage, _ = check("how much is it?", provider=provider)
    assert bot.sent == []
    assert storage.claims == []


def test_empty_message_is_not_checked():
    bot, _, provider = check("")
    assert provider.requests == []
    assert bot.sent == []


def test_admins_hear_of_urgency_once_per_wait():
    bot, _, _ = check("urgent!", storage=_Storage(claim=False))
    assert bot.sent == []


def test_storage_failure_still_reaches_the_admins(caplog):
    with caplog.at_level(logging.WARNING):
        bot, _, _ = check("urgent!", storage=_Storage(claim=OSError("database is down")))
    assert [m["chat_id"] for m in bot.sent] == [1, 2]
    assert "database is down" in caplog.text


def test_unreachable_admin_does_not_stop_the_rest():
    bot, _, _ = check("urgent!", bot=_Bot(unreachable=[1]))
    assert [m["chat_id"] for m in bot.sent] == [2]


@pytest.fixture()
def user_writes(monkeypatch):
    """Drive the private message handler; returns the urgency checks it started."""
    checks = []

    async def fake_topic(bot, redis, config, user_data):
        return 7

    async def no_sleep(_):
        return None

    def fake_check(*args):
        checks.append(args)

    monkeypatch.setattr(private_message, "get_or_create_forum_topic", fake_topic)
    monkeypatch.setattr(private_message, "run_ai_layer", lambda *args, **kwargs: None)
    monkeypatch.setattr(private_message, "run_urgent_check", fake_check)
    monkeypatch.setattr(
        private_message, "asyncio", SimpleNamespace(sleep=no_sleep, create_task=lambda value: None)
    )
    manager = SimpleNamespace(
        config=SimpleNamespace(bot=SimpleNamespace(GROUP_ID=GROUP)),
        text_message=SimpleNamespace(get=lambda key: key),
    )

    class _Storage:
        async def append_conversation(self, user_id, role, text):
            return None

        async def claim_first_message(self, user_id):
            return False

    async def reply(text):
        async def delete():
            return None

        return SimpleNamespace(delete=delete)

    async def forward(**kwargs):
        return SimpleNamespace(message_id=900)

    def send(policy):
        message = SimpleNamespace(bot=_Bot(), text="I paid and got nothing", caption=None, reply=reply, forward=forward)
        asyncio.run(private_message.handle_incoming_message(
            message, manager, _Storage(), make_user(), policy_engine=policy, llm_provider=object(),
        ))
        return checks

    return send


def test_every_user_message_is_checked_with_its_topic_copy(user_writes):
    (args,) = user_writes(engine())
    assert args[-1] == 900


def test_no_check_while_it_is_off(user_writes):
    assert user_writes(load_policy_from_dict({})) == []
