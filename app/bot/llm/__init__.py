from __future__ import annotations

import logging

from app.config import AIConfig

from .base import LLMProvider

logger = logging.getLogger(__name__)


def get_provider(config: AIConfig) -> LLMProvider | None:
    """
    Build an LLM provider from config, or return None when disabled.

    - PROVIDER="none"            -> None (default; no SDK needed)
    - empty API_KEY              -> None (graceful: warn and disable)
    - PROVIDER="openai_compatible" -> OpenAICompatibleProvider
    - anything else              -> ValueError (fail fast on typos)
    """
    if config.PROVIDER == "none":
        return None

    if not config.API_KEY:
        logger.warning("AI_PROVIDER=%s but AI_API_KEY is empty; LLM disabled.", config.PROVIDER)
        return None

    if config.PROVIDER == "openai_compatible":
        from .openai_compatible import OpenAICompatibleProvider

        try:
            return OpenAICompatibleProvider(
                base_url=config.BASE_URL,
                api_key=config.API_KEY,
                model=config.MODEL,
                timeout=config.TIMEOUT_S,
            )
        except ImportError:
            logger.warning(
                "AI_PROVIDER=openai_compatible but the 'openai' package is not "
                "installed; LLM disabled. Install requirements-ai.txt (or build "
                "with INSTALL_AI=1)."
            )
            return None

    raise ValueError(f"Unknown AI_PROVIDER: {config.PROVIDER!r}")


__all__ = [
    "LLMProvider",
    "get_provider",
]
