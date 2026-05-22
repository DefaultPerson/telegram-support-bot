from __future__ import annotations

from typing import Protocol, runtime_checkable

# A chat message in OpenAI format: {"role": "system"|"user"|"assistant", "content": str}
ChatMessage = dict[str, str]


@runtime_checkable
class LLMProvider(Protocol):
    """Provider-agnostic interface for the optional LLM layer."""

    async def draft_reply(self, messages: list[ChatMessage]) -> str | None:
        """Given a chat transcript, draft the next assistant reply (or None)."""
        ...
