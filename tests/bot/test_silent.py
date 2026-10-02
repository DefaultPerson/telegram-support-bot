import asyncio
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.bot.handlers.group import command as group_command
from app.bot.utils.redis.models import UserData

# The first handler of the topic router is /silent.
silent = group_command.router.message.handlers[0].callback


def make_user(mode: bool = False, silent_id: int | None = None) -> UserData:
    return UserData(
        message_thread_id=7, message_silent_id=silent_id, message_silent_mode=mode,
        id=42, full_name="User", username="-",
    )


class _Storage:
    def __init__(self, user: UserData) -> None:
        self.user = user
        self.saved: UserData | None = None

    async def get_by_message_thread_id(self, thread_id):
        return self.user

    async def update_user(self, id_, data):
        self.saved = data


class _Bot:
    def __init__(self) -> None:
        self.unpinned: list = []

    async def unpin_chat_message(self, chat_id, message_id=None):
        self.unpinned.append(message_id)


class _Message:
    def __init__(self, reply_error=None, pin_error=None) -> None:
        self.message_thread_id = 7
        self.chat = SimpleNamespace(id=-100)
        self.bot = _Bot()
        self.reply_error = reply_error
        self.pin_error = pin_error

    async def reply(self, text):
        if self.reply_error is not None:
            raise self.reply_error

        async def pin(disable_notification=False):
            if self.pin_error is not None:
                raise self.pin_error

        return SimpleNamespace(message_id=55, pin=pin)


def toggle(user: UserData, message: _Message) -> UserData:
    storage = _Storage(user)
    manager = SimpleNamespace(text_message=SimpleNamespace(get=lambda key: key))
    asyncio.run(silent(message, manager, storage))
    return storage.saved


def bad_request():
    return TelegramBadRequest(method=None, message="Bad Request: not enough rights")


def test_silent_mode_pins_its_status():
    saved = toggle(make_user(), _Message())
    assert saved.message_silent_mode is True
    assert saved.message_silent_id == 55


@pytest.mark.parametrize("message", [
    _Message(reply_error=bad_request()),
    _Message(pin_error=bad_request()),
])
def test_silent_mode_is_on_even_when_the_status_is_not_pinned(message):
    saved = toggle(make_user(), message)
    assert saved.message_silent_mode is True
    assert saved.message_silent_id is None


def test_turning_off_unpins_the_status():
    message = _Message()
    saved = toggle(make_user(mode=True, silent_id=55), message)
    assert saved.message_silent_mode is False
    assert message.bot.unpinned == [55]


def test_turning_off_without_a_pinned_status_unpins_nothing():
    # An unpin without a message id would drop the group's latest pin.
    message = _Message()
    saved = toggle(make_user(mode=True), message)
    assert saved.message_silent_mode is False
    assert message.bot.unpinned == []
