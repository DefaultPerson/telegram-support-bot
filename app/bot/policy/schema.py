from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ActionType = Literal[
    "suppress_topic_creation",
    "suppress_group_notify",
    "auto_reply",
    "close_topic",
    "escalate",
]


class Action(BaseModel):
    """A single instruction attached to a rule."""
    model_config = ConfigDict(extra="forbid")

    type: ActionType
    template_key: str | None = None


class Rule(BaseModel):
    """A matcher (`when`) paired with the actions to apply on match."""
    model_config = ConfigDict(extra="forbid")

    id: str
    when: dict[str, Any]
    actions: list[Action] = Field(default_factory=list)


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language_fallback: str = "en"


class AISection(BaseModel):
    """LLM-related options. The engine never executes actions from here."""
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    system_prompt_path: str | None = None
    max_context_messages: int = 12


class PolicyDocument(BaseModel):
    """Top-level schema of a policy YAML file."""
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    defaults: Defaults = Field(default_factory=Defaults)
    variables: dict[str, str] = Field(default_factory=dict)
    templates: dict[str, dict[str, str]] = Field(default_factory=dict)
    rules: list[Rule] = Field(default_factory=list)
    ai: AISection = Field(default_factory=AISection)
