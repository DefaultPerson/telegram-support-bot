from __future__ import annotations

import base64
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


def load_policy_from_yaml(text: str) -> PolicyEngine:
    """Build a PolicyEngine from a YAML string."""
    return load_policy_from_dict(yaml.safe_load(text) or {})


def load_policy(config: PolicyConfig) -> PolicyEngine:
    """
    Load and validate the policy.

    Prefers the YAML file at ``config.PATH``; if it does not exist, falls back
    to base64-encoded YAML in ``config.INLINE_B64`` (useful where mounting a
    file is awkward). Raises FileNotFoundError if neither source is available.
    """
    path = Path(config.PATH)
    if path.exists():
        return load_policy_from_yaml(path.read_text(encoding="utf-8"))

    if config.INLINE_B64:
        decoded = base64.b64decode(config.INLINE_B64).decode("utf-8")
        return load_policy_from_yaml(decoded)

    raise FileNotFoundError(
        f"Policy file not found ({path}) and POLICY_YAML_B64 is empty"
    )
