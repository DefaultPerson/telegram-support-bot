import asyncio
from types import SimpleNamespace

import pytest

from app.bot.handlers.private import command, my_chat_member
from app.bot.utils.redis.models import UserData


def make_user(thread_id=None) -> UserData:
    return UserData(
        message_thread_id=thread_id, message_silent_id=None, message_silent_mode=False,
        id=42, full_name="User", username="-", language_code="en",
    )


class _Storage:
    def __init__(self) -> None:
        self.updates = 0

    async def update_user(self, id_, data):
        self.updates += 1


class _Bot:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


def make_manager(topic_on_start: bool):
    async def delete_message(_):
        return None

    return SimpleNamespace(
        config=SimpleNamespace(bot=SimpleNamespace(GROUP_ID=-100, TOPIC_ON_START=topic_on_start)),
        text_message=SimpleNamespace(get=lambda key: key + " {name}"),
        delete_message=delete_message,
    )


@pytest.fixture()
def start_handler(monkeypatch):
    """The /start handler with the menu stubbed; topic creation and the menu are recorded."""
    created = []
    shown = []

    async def fake_topic(bot, redis, config, user_data):
        created.append(user_data.id)
        return 7

    async def main_menu(manager):
        shown.append("main_menu")

    monkeypatch.setattr(command, "get_or_create_forum_topic", fake_topic)
    monkeypatch.setattr(command.Window, "main_menu", main_menu)
    return command.router.message.handlers[0].callback, created, shown


def test_start_creates_topic_by_default(start_handler):
    handler, created, _ = start_handler
    message = SimpleNamespace(bot=_Bot())

    asyncio.run(handler(message, make_manager(True), _Storage(), make_user()))

    assert created == [42]


def test_start_only_greets_when_topic_waits_for_first_message(start_handler):
    handler, created, shown = start_handler
    message = SimpleNamespace(bot=_Bot())

    asyncio.run(handler(message, make_manager(False), _Storage(), make_user()))

    assert created == []
    # The greeting is the main menu, whose text the policy's texts.main_menu replaces.
    assert shown == ["main_menu"]


def lifecycle(status: str, user: UserData, topic_on_start: bool, policy_engine=None):
    bot = _Bot()
    storage = _Storage()
    update = SimpleNamespace(bot=bot, new_chat_member=SimpleNamespace(status=status))
    handler = my_chat_member.router.my_chat_member.handlers[0].callback
    asyncio.run(handler(update, storage, user, make_manager(topic_on_start), policy_engine))
    return bot, storage


def test_lifecycle_without_topic_is_not_posted_to_general():
    bot, storage = lifecycle("kicked", make_user(thread_id=None), topic_on_start=False)

    assert bot.sent == []
    assert storage.updates == 1  # the state is still recorded


def test_lifecycle_with_topic_still_notifies_in_lazy_mode():
    bot, _ = lifecycle("member", make_user(thread_id=7), topic_on_start=False)

    assert len(bot.sent) == 1
    assert bot.sent[0]["message_thread_id"] == 7


def test_lifecycle_without_topic_keeps_old_behaviour_by_default():
    bot, _ = lifecycle("kicked", make_user(thread_id=None), topic_on_start=True)

    assert len(bot.sent) == 1
    assert bot.sent[0]["message_thread_id"] is None


def test_policy_still_suppresses_lifecycle_notice_in_lazy_mode():
    from app.bot.policy import load_policy_from_dict

    engine = load_policy_from_dict({
        "rules": [{"id": "quiet", "when": {"event_type": ["user_started", "user_stopped"]},
                   "actions": [{"type": "suppress_group_notify"}]}],
    })

    bot, _ = lifecycle("member", make_user(thread_id=7), topic_on_start=False, policy_engine=engine)

    assert bot.sent == []
