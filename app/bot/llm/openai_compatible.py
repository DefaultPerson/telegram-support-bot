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
        max_tokens: int = 4096,
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
        choices = response.choices or []
        if not choices:
            logger.warning("LLM returned no choices; skipping the draft.")
            return None

        choice = choices[0]
        content = (choice.message.content or "").strip()
        if content:
            return content

        # Reasoning models bill their thinking tokens against max_tokens, so a
        # budget that is too small comes back as finish_reason="length" with an
        # empty message instead of an error. Say so instead of failing silently.
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "length":
            logger.warning(
                "LLM hit the %s-token cap before producing a reply; raise AI_MAX_TOKENS.",
                self._max_tokens,
            )
        else:
            logger.warning("LLM returned an empty reply (finish_reason=%s).", finish_reason)
        return None
