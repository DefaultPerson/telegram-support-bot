import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.bot.handlers.private.windows import Window
from app.bot.manager import Manager
from app.bot.policy import load_policy_from_dict
from app.bot.utils.texts import TextMessage


@pytest.fixture()
def overrides(monkeypatch):
    """Apply the policy's texts as the bot does at startup, undone after the test."""
    def apply(texts):
        monkeypatch.setattr(TextMessage, "overrides", load_policy_from_dict({"texts": texts}).texts)

    return apply


def test_without_the_section_texts_are_built_in():
    assert load_policy_from_dict({}).texts == {}
    assert TextMessage("ru").get("main_menu").startswith("<b>Здравствуйте!</b>")


def test_unknown_key_is_rejected():
    with pytest.raises(ValidationError, match="unknown keys"):
        load_policy_from_dict({"texts": {"main_menuu": {"en": "Hi"}}})


def test_unsupported_language_is_rejected():
    with pytest.raises(ValidationError, match="unsupported languages"):
        load_policy_from_dict({"texts": {"main_menu": {"de": "Hallo"}}})


def test_override_replaces_the_text_in_its_language(overrides):
    builtin_ru = TextMessage("ru").get("main_menu")
    overrides({"main_menu": {"en": "Welcome to Example support."}})

    assert TextMessage("en").get("main_menu") == "Welcome to Example support."
    # Unsupported user languages fall back to English, and so does the override.
    assert TextMessage("de").get("main_menu") == "Welcome to Example support."
    # A language without an override keeps the built-in text.
    assert TextMessage("ru").get("main_menu") == builtin_ru
    # Every other key is untouched.
    assert TextMessage("en").get("message_sent") == "<b>Message sent!</b> Expect a response."


def test_start_shows_the_overridden_welcome(overrides):
    overrides({"main_menu": {"ru": "Привет, {full_name}! Пишите {сюда"}})
    sent = []

    async def send_message(text, **kwargs):
        sent.append(text)

    async def set_state(state):
        return None

    manager = Manager("💎", {}, "ru")
    manager.user = SimpleNamespace(full_name="Ann")
    manager.send_message = send_message
    manager.state = SimpleNamespace(set_state=set_state)

    asyncio.run(Window.main_menu(manager))

    # A stray brace leaves the text unformatted instead of failing /start.
    assert sent == ["Привет, {full_name}! Пишите {сюда"]


def test_welcome_override_can_name_the_user(overrides):
    overrides({"main_menu": {"en": "Hi, {full_name}!"}})
    sent = []

    async def send_message(text, **kwargs):
        sent.append(text)

    async def set_state(state):
        return None

    manager = Manager("💎", {}, "en")
    manager.user = SimpleNamespace(full_name="Ann")
    manager.send_message = send_message
    manager.state = SimpleNamespace(set_state=set_state)

    asyncio.run(Window.main_menu(manager))
    assert sent == ["Hi, <b>Ann</b>!"]
