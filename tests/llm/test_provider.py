import asyncio

from app.bot.llm import LLMProvider


class _FakeProvider:
    async def draft_reply(self, messages):
        last = messages[-1]["content"] if messages else ""
        return f"reply to {last}"


def test_fake_provider_satisfies_protocol():
    assert isinstance(_FakeProvider(), LLMProvider)


def test_fake_provider_callable():
    draft = asyncio.run(
        _FakeProvider().draft_reply(
            [{"role": "system", "content": "p"}, {"role": "user", "content": "hi"}]
        )
    )
    assert draft == "reply to hi"
