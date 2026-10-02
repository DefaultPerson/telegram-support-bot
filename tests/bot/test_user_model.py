from datetime import datetime, timezone

from app.bot.utils.redis import models
from app.bot.utils.redis.models import UserData


def make_user(**kwargs) -> UserData:
    return UserData(
        message_thread_id=None, message_silent_id=None, message_silent_mode=False,
        id=42, full_name="User", username=None, **kwargs,
    )


def test_created_at_is_the_time_of_creation(monkeypatch):
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2030, 1, 2, 0, 4, 5, tzinfo=timezone.utc).astimezone(tz)

    # Not the time the module was imported at.
    monkeypatch.setattr(models, "datetime", _Clock)
    assert make_user().created_at == "2030-01-02 03:04:05 UTC+03:00"


def test_created_at_keeps_its_format():
    created_at = make_user().created_at
    assert created_at.endswith(" UTC+03:00")
    datetime.strptime(created_at.removesuffix(" UTC+03:00"), "%Y-%m-%d %H:%M:%S")


def test_a_loaded_created_at_is_kept():
    assert make_user(created_at="2024-05-06 07:08:09 UTC+03:00").created_at == "2024-05-06 07:08:09 UTC+03:00"
