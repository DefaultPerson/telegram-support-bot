from __future__ import annotations

from .actions import apply_action
from .context import EvalContext
from .decision import Decision
from .matchers import matches
from .schema import AISection, PolicyDocument, RemindersSection


class PolicyEngine:
    """Evaluates a validated PolicyDocument against an EvalContext."""

    def __init__(self, document: PolicyDocument) -> None:
        self.document = document

    @property
    def ai(self) -> AISection:
        return self.document.ai

    @property
    def reminders(self) -> RemindersSection:
        return self.document.reminders

    @property
    def texts(self) -> dict[str, dict[str, str]]:
        return self.document.texts

    def evaluate(self, ctx: EvalContext) -> Decision:
        """
        Apply every matching rule in declaration order and aggregate the
        resulting actions into a single Decision.
        """
        decision = Decision()
        for rule in self.document.rules:
            if matches(rule.when, ctx):
                for action in rule.actions:
                    apply_action(action, ctx, self.document, decision)
        return decision
