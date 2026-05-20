from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class ClassifyResult:
    """Outcome of a single classify-and-draft call."""
    category: str | None = None
    draft: str | None = None
    confidence: float | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """Provider-agnostic interface for the optional LLM layer."""

    async def classify_and_draft(
        self,
        text: str,
        language: str,
        categories: list[str],
        system_prompt: str,
    ) -> ClassifyResult:
        """Classify an incoming message and draft a suggested reply."""
        ...
