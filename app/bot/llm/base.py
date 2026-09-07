from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# A chat message in OpenAI format: {"role": "system"|"user"|"assistant", "content": ...}.
# `content` is a plain string, or a list of parts ({"type": "text"|"image_url", ...})
# when the turn carries attachments.
ChatMessage = dict[str, Any]


@runtime_checkable
class LLMProvider(Protocol):
    """Provider-agnostic interface for the optional LLM layer."""

    async def draft_reply(self, messages: list[ChatMessage]) -> str | None:
        """Given a chat transcript, draft the next assistant reply (or None)."""
        ...
