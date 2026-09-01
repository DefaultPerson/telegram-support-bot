import pytest

from app.bot.llm import get_provider
from app.config import AIConfig


def make_cfg(**kwargs):
    base = dict(
        PROVIDER="none",
        BASE_URL="https://openrouter.ai/api/v1",
        API_KEY="",
        MODEL="openai/gpt-5-nano",
        SYSTEM_PROMPT_PATH="config/system_prompt.txt",
        TIMEOUT_S=8,
    )
    base.update(kwargs)
    return AIConfig(**base)


def test_none_provider_returns_none():
    assert get_provider(make_cfg(PROVIDER="none")) is None


def test_empty_api_key_disables():
    assert get_provider(make_cfg(PROVIDER="openai_compatible", API_KEY="")) is None


def test_max_tokens_defaults_to_a_bounded_value():
    assert make_cfg().MAX_TOKENS == 1024


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        get_provider(make_cfg(PROVIDER="bogus", API_KEY="sk-test"))
