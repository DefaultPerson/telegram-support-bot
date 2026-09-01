import asyncio
from types import SimpleNamespace

from app.bot.llm.openai_compatible import OpenAICompatibleProvider


class _FakeCompletions:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="  draft  "))]
        )


def make_provider(max_tokens: int = 1024) -> tuple[OpenAICompatibleProvider, _FakeCompletions]:
    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    completions = _FakeCompletions()
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider._model = "openai/gpt-5.4-nano"
    provider._max_tokens = max_tokens
    return provider, completions


def test_max_tokens_is_always_sent():
    """Without it the provider reserves the full output window and 402s."""
    provider, completions = make_provider(max_tokens=256)

    asyncio.run(provider.draft_reply([{"role": "user", "content": "hi"}]))

    assert completions.kwargs["max_tokens"] == 256
    assert completions.kwargs["model"] == "openai/gpt-5.4-nano"


def test_draft_is_stripped():
    provider, _ = make_provider()

    assert asyncio.run(provider.draft_reply([{"role": "user", "content": "hi"}])) == "draft"
