from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AutoReply:
    """A rendered auto-reply and the options that govern its delivery."""
    text: str
    template_key: str
    once: bool = False
    suppress_draft: bool = True


@dataclass
class Decision:
    """
    Result of evaluating the policy rules against an EvalContext.

    Holds only data — applying it to Telegram/Redis is the caller's job.
    """
    auto_replies: list[AutoReply] = field(default_factory=list)
    suppress_topic_creation: bool = False
    suppress_group_notify: bool = False
    close_topic: bool = False
    escalate: bool = False

    @property
    def is_noop(self) -> bool:
        """True when the decision carries no instruction."""
        return not (
            self.auto_replies
            or self.suppress_topic_creation
            or self.suppress_group_notify
            or self.close_topic
            or self.escalate
        )

    @property
    def suppresses_draft(self) -> bool:
        """True when an auto-reply already answers and the LLM draft is not needed."""
        return any(reply.suppress_draft for reply in self.auto_replies)
