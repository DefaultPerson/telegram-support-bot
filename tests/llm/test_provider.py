import asyncio

from app.bot.llm import LLMProvider
from app.bot.llm.base import ClassifyResult
from app.bot.llm.openai_compatible import OpenAICompatibleProvider


class _FakeProvider:
    async def classify_and_draft(self, text, language, categories, system_prompt):
        return ClassifyResult(category="other", draft=f"reply to {text}", confidence=0.5)


def test_fake_provider_satisfies_protocol():
    assert isinstance(_FakeProvider(), LLMProvider)


def test_fake_provider_callable():
    result = asyncio.run(
        _FakeProvider().classify_and_draft("hi", "en", ["other"], "prompt")
    )
    assert result.category == "other"
    assert result.draft == "reply to hi"
    assert result.confidence == 0.5


def test_parse_valid_json():
    result = OpenAICompatibleProvider._parse(
        '{"category": "price", "draft": "see rules", "confidence": 0.9}'
    )
    assert result.category == "price"
    assert result.draft == "see rules"
    assert result.confidence == 0.9


def test_parse_invalid_json_returns_empty():
    result = OpenAICompatibleProvider._parse("not json at all")
    assert result.category is None
    assert result.draft is None
    assert result.confidence is None


def test_parse_bad_confidence_coerced_to_none():
    result = OpenAICompatibleProvider._parse(
        '{"category": "x", "draft": "y", "confidence": "high"}'
    )
    assert result.category == "x"
    assert result.confidence is None
