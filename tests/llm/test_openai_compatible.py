import asyncio
import logging
from types import SimpleNamespace

from app.bot.llm.openai_compatible import OpenAICompatibleProvider


def make_choice(content, finish_reason="stop"):
    return SimpleNamespace(
        message=SimpleNamespace(content=content), finish_reason=finish_reason
    )


class _FakeCompletions:
    def __init__(self, choices=None) -> None:
        self.kwargs: dict = {}
        self._choices = [make_choice("  draft  ")] if choices is None else choices

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(choices=self._choices)


def make_provider(
    max_tokens: int = 4096, choices=None
) -> tuple[OpenAICompatibleProvider, _FakeCompletions]:
    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    completions = _FakeCompletions(choices)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider._model = "openai/gpt-5.6-luna"
    provider._max_tokens = max_tokens
    return provider, completions


def test_max_tokens_is_always_sent():
    """Without it the provider reserves the full output window and 402s."""
    provider, completions = make_provider(max_tokens=256)

    asyncio.run(provider.draft_reply([{"role": "user", "content": "hi"}]))

    assert completions.kwargs["max_tokens"] == 256
    assert completions.kwargs["model"] == "openai/gpt-5.6-luna"


def test_draft_is_stripped():
    provider, _ = make_provider()

    assert asyncio.run(provider.draft_reply([{"role": "user", "content": "hi"}])) == "draft"


def test_reasoning_budget_exhaustion_is_reported(caplog):
    """A reasoning model that spends max_tokens thinking returns no content."""
    provider, _ = make_provider(
        max_tokens=16, choices=[make_choice("", finish_reason="length")]
    )

    with caplog.at_level(logging.WARNING):
        draft = asyncio.run(provider.draft_reply([{"role": "user", "content": "hi"}]))

    assert draft is None
    assert "raise AI_MAX_TOKENS" in caplog.text


def test_empty_reply_is_reported(caplog):
    provider, _ = make_provider(choices=[make_choice(None)])

    with caplog.at_level(logging.WARNING):
        draft = asyncio.run(provider.draft_reply([{"role": "user", "content": "hi"}]))

    assert draft is None
    assert "empty reply" in caplog.text


def test_missing_choices_do_not_raise(caplog):
    """Some gateways return an empty choices list instead of an error."""
    provider, _ = make_provider(choices=[])

    with caplog.at_level(logging.WARNING):
        draft = asyncio.run(provider.draft_reply([{"role": "user", "content": "hi"}]))

    assert draft is None
    assert "no choices" in caplog.text
