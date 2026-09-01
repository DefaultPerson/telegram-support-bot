from __future__ import annotations

import re

# Patterns for values that must never reach logs or the developer chat.
# Order matters only for readability; every pattern is applied.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # OpenAI / OpenRouter style API keys: sk-..., sk-or-v1-...
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    # Telegram bot tokens: 1234567890:AA...
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_\-]{30,}"),
    # Authorization headers.
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    # Key identifiers embedded in provider dashboard URLs.
    re.compile(r"(?i)(/keys/)[A-Za-z0-9_\-]{16,}"),
    # Any remaining long hex blob (key hashes, digests).
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
)

_PLACEHOLDER = "[redacted]"

DEFAULT_LIMIT = 500


def redact(text: str, limit: int | None = DEFAULT_LIMIT) -> str:
    """
    Strip credential-looking substrings from ``text`` and optionally truncate it.

    Provider errors (OpenRouter 402, for example) embed dashboard URLs containing
    key hashes and can run for thousands of characters, so anything derived from a
    third-party error must pass through here before being logged or forwarded.

    :param text: The raw text to sanitize.
    :param limit: Maximum length of the result, or None to keep it unbounded.
    :return: The sanitized text.
    """
    for pattern in _SECRET_PATTERNS:
        # Keep the "/keys/" prefix so the message still reads sensibly.
        replacement = rf"\1{_PLACEHOLDER}" if pattern.groups else _PLACEHOLDER
        text = pattern.sub(replacement, text)

    if limit is not None and len(text) > limit:
        text = f"{text[:limit]}… (truncated)"

    return text
