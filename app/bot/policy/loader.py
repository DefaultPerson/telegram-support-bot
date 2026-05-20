from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.config import PolicyConfig

from .engine import PolicyEngine
from .schema import PolicyDocument


def load_policy_from_dict(raw: dict[str, Any]) -> PolicyEngine:
    """Build a PolicyEngine from an already-parsed mapping (used by tests)."""
    document = PolicyDocument.model_validate(raw or {})
    return PolicyEngine(document)


def load_policy(config: PolicyConfig) -> PolicyEngine:
    """
    Load and validate the policy YAML referenced by ``config.PATH``.

    Raises FileNotFoundError if the file is missing — callers should only invoke
    this when ``config.ENABLED`` is true, so a missing file is a fail-fast error
    rather than silently running without the expected filtering.
    """
    path = Path(config.PATH)
    if not path.exists():
        raise FileNotFoundError(f"Policy file not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return load_policy_from_dict(raw)
