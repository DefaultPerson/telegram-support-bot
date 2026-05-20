from __future__ import annotations

import json
import logging

from .base import ClassifyResult

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider:
    """
    LLM provider for any OpenAI-compatible Chat Completions endpoint
    (OpenRouter, OpenAI, vLLM, LM Studio, ...).

    The ``openai`` SDK is imported lazily so the base installation does not
    require it when the LLM layer is disabled.
    """

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self._model = model

    async def classify_and_draft(
        self,
        text: str,
        language: str,
        categories: list[str],
        system_prompt: str,
    ) -> ClassifyResult:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._build_user_prompt(text, language, categories)},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        return self._parse(content)

    @staticmethod
    def _build_user_prompt(text: str, language: str, categories: list[str]) -> str:
        cats = ", ".join(categories) if categories else "any"
        return (
            f"Incoming support message (language={language}):\n"
            f'"""\n{text}\n"""\n\n'
            f"Allowed categories: {cats}.\n"
            "Respond with a JSON object with exactly these keys: "
            '{"category": <one of the allowed categories or null>, '
            '"draft": <a suggested reply written in the same language as the message>, '
            '"confidence": <a number between 0 and 1>}.'
        )

    @staticmethod
    def _parse(content: str) -> ClassifyResult:
        try:
            data = json.loads(content)
        except (ValueError, TypeError):
            logger.warning("LLM returned non-JSON content: %r", content)
            return ClassifyResult()

        confidence = data.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (ValueError, TypeError):
            confidence = None

        return ClassifyResult(
            category=data.get("category"),
            draft=data.get("draft"),
            confidence=confidence,
        )
