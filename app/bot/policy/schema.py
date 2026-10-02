from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    # auto_reply only: send this template to a user at most once
    # (None falls back to defaults.auto_reply_once) ...
    once: bool | None = None
    # ... and whether sending it skips the LLM draft for the message.
    suppress_draft: bool = True


class Rule(BaseModel):
    """A matcher (`when`) paired with the actions to apply on match."""
    model_config = ConfigDict(extra="forbid")

    id: str
    when: dict[str, Any]
    actions: list[Action] = Field(default_factory=list)


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language_fallback: str = "en"
    # Whether auto_reply actions without an explicit `once` repeat or not.
    auto_reply_once: bool = False


class AICategory(BaseModel):
    """A conversation category the LLM picks for the user's first message."""
    model_config = ConfigDict(extra="forbid")

    # Short and ASCII: it travels in the /ai_auto confirmation button's
    # callback data, which Telegram caps at 64 bytes.
    key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    title: str
    icon: str = ""
    # Never answered automatically: /ai_auto refuses to switch it on.
    needs_human: bool = False
    # Message every admin in BOT_DEV_IDS with a link to the topic.
    notify_admins: bool = False


class AISection(BaseModel):
    """LLM-related options. The engine never executes actions from here."""
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    system_prompt_path: str | None = None
    max_context_messages: int = 12
    # Record every draft and what became of it (sent, skipped, ...) for /ai_stats.
    log_drafts: bool = False
    # Empty: the first message is not classified.
    categories: list[AICategory] = Field(default_factory=list)
    # /ai_auto warns before switching a category on below this share of sent
    # drafts or with fewer reviewed drafts than auto_min_drafts.
    auto_threshold: float = 0.95
    auto_min_drafts: int = 20

    def category(self, key: str | None) -> AICategory | None:
        """Return the configured category with this key, if any."""
        return next((c for c in self.categories if c.key == key), None)


class RemindersSection(BaseModel):
    """Reminders in the topic when a user waits too long for a reply."""
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # Minutes since the user's first unanswered message; one reminder each.
    after_minutes: list[int] = Field(default_factory=lambda: [180, 1440])
    # Category keys (ai.categories) whose conversations get no reminders.
    skip_categories: list[str] = Field(default_factory=list)
    check_interval_minutes: int = Field(default=10, ge=1)

    @field_validator("after_minutes")
    @classmethod
    def _sorted_positive(cls, value: list[int]) -> list[int]:
        if any(minutes < 1 for minutes in value):
            raise ValueError("after_minutes must be positive")
        return sorted(set(value))


class PolicyDocument(BaseModel):
    """Top-level schema of a policy YAML file."""
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    defaults: Defaults = Field(default_factory=Defaults)
    variables: dict[str, str] = Field(default_factory=dict)
    templates: dict[str, dict[str, str]] = Field(default_factory=dict)
    rules: list[Rule] = Field(default_factory=list)
    ai: AISection = Field(default_factory=AISection)
    reminders: RemindersSection = Field(default_factory=RemindersSection)

    @model_validator(mode="after")
    def _known_skip_categories(self) -> "PolicyDocument":
        keys = {c.key for c in self.ai.categories}
        unknown = [key for key in self.reminders.skip_categories if key not in keys]
        if unknown:
            raise ValueError(f"reminders.skip_categories not in ai.categories: {unknown}")
        return self
