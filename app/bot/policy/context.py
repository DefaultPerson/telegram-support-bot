from __future__ import annotations

from dataclasses import dataclass

# Event types understood by the policy engine.
EVENT_USER_MESSAGE = "user_message"
EVENT_USER_STARTED = "user_started"
EVENT_USER_STOPPED = "user_stopped"
EVENT_TOPIC_CREATED = "topic_created"


@dataclass
class EvalContext:
    """
    Telegram-agnostic input for the policy engine.

    :param event_type: One of the EVENT_* constants.
    :param text: The user's message text (empty for non-message events).
    :param language: Two-letter language code used to render templates.
    """
    event_type: str
    text: str = ""
    language: str = "en"
