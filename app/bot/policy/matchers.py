from __future__ import annotations

import re
from typing import Any

from .context import EvalContext

# Matches a URL, a t.me link, or an @username mention (3+ chars).
_LINK_RE = re.compile(r"(https?://|t\.me/|@[A-Za-z0-9_]{3,})", re.IGNORECASE)


def matches(clause: dict[str, Any], ctx: EvalContext) -> bool:
    """
    Evaluate a `when` clause against the context.

    Supported shapes:
      - {"all": [clause, ...]}  — every sub-clause must match
      - {"any": [clause, ...]}  — at least one sub-clause must match
      - {leaf: value, ...}      — every leaf condition must match (implicit AND)
    """
    if "all" in clause:
        return all(matches(sub, ctx) for sub in clause["all"])
    if "any" in clause:
        return any(matches(sub, ctx) for sub in clause["any"])
    return all(_match_leaf(key, value, ctx) for key, value in clause.items())


def _match_leaf(key: str, value: Any, ctx: EvalContext) -> bool:
    if key == "event_type":
        allowed = [value] if isinstance(value, str) else list(value)
        return ctx.event_type in allowed

    if key == "keywords_any":
        if isinstance(value, dict):
            words = value.get("list", [])
            min_matches = value.get("min_matches", 1)
        else:
            words = value
            min_matches = 1
        low = ctx.text.lower()
        count = sum(1 for w in words if str(w).lower() in low)
        return count >= min_matches

    if key == "regex":
        return re.search(value, ctx.text) is not None

    if key == "message_length":
        length = len(ctx.text.strip())
        if "min" in value and length < value["min"]:
            return False
        if "max" in value and length > value["max"]:
            return False
        return True

    if key == "has_link":
        present = _LINK_RE.search(ctx.text) is not None
        return present == bool(value)

    raise ValueError(f"Unknown matcher: {key!r}")
