from __future__ import annotations

import logging

from .base import ChatMessage

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider:
    """
    LLM provider for any OpenAI-compatible Chat Completions endpoint
    (OpenRouter, OpenAI, vLLM, LM Studio, ...).

    The ``openai`` SDK is imported lazily so the base installation does not
    require it when the LLM layer is disabled.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int,
        max_tokens: int = 1024,
    ) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self._model = model
        self._max_tokens = max_tokens

    async def draft_reply(self, messages: list[ChatMessage]) -> str | None:
        # max_tokens is required: providers that bill per request reserve the
        # model's full output window when it is omitted and reject the call if
        # the remaining balance cannot cover the reservation.
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=self._max_tokens,
        )
        content = response.choices[0].message.content
        return content.strip() if content else None
