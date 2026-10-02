from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone


def _now() -> str:
    """Current time in UTC+3, the format ``created_at`` is stored in."""
    return datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M:%S %Z")


@dataclass
class UserData:
    """Data class representing user information."""
    message_thread_id: int | None
    message_silent_id: int | None
    message_silent_mode: bool

    id: int
    full_name: str
    username: str | None
    state: str = "member"
    is_banned: bool = False
    language_code: str | None = None
    # Computed per record: a plain default would freeze the import time.
    created_at: str = field(default_factory=_now)
    # Conversation status used by /close and /escalate. Defaults keep old records loadable.
    status: str = "open"  # "open" | "closed" | "escalated"

    def to_dict(self) -> dict:
        """
        Converts UserData object to a dictionary.

        :return: Dictionary representation of UserData.
        """
        return asdict(self)
