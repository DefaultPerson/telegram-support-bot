from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Decision:
    """
    Result of evaluating the policy rules against an EvalContext.

    Holds only data — applying it to Telegram/Redis is the caller's job.
    """
    auto_replies: list[str] = field(default_factory=list)
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
