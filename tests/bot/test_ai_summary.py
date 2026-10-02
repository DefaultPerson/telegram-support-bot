import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.bot.policy import load_policy_from_dict
from app.bot.policy.schema import AISection
from app.bot.utils.policy_runtime import run_ai_draft
from app.bot.utils.redis.models import UserData

URL = "data:image/jpeg;base64,AAAA"
SUMMARY = "Wants a refund for order 77."
NEW_SUMMARY = "Wants a refund for order 77; support asked for the receipt."
DRAFT = "Refunds take 3 days."


def turns(*ids):
    """Transcript turns as the draft sees them; odd ids are the customer's."""
    return [{"role": "user" if i % 2 else "assistant", "content": f"turn {i}"} for i in ids]


class _Storage:
    def __init__(self, count, summary=None, fail_store=False) -> None:
        self.rows = [{"id": i, **turn} for i, turn in zip(range(1, count + 1), turns(*range(1, count + 1)))]
        self.summary = summary
        self.fail_store = fail_store
        self.stored: list[tuple] = []
        self.draft = None

    async def get_conversation(self, user_id, limit):
        return [{"role": r["role"], "content": r["content"]} for r in self.rows[-limit:]]

    async def get_conversation_summary(self, user_id):
        return self.summary

    async def get_conversation_since(self, user_id, after_id, window):
        cut = len(self.rows) - window
        return [r for n, r in enumerate(self.rows) if r["id"] > after_id or n >= cut]

    async def set_conversation_summary(self, user_id, summary, covered_id):
        if self.fail_store:
            raise ConnectionError("database is gone")
        self.stored.append((summary, covered_id))
        self.summary = (summary, covered_id)

    async def set_ai_draft(self, user_id, text):
        self.draft = text


class _Bot:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class _Provider:
    """Answers the summary request with ``summary`` (or ``fail``) and the draft with DRAFT."""

    def __init__(self, summary=NEW_SUMMARY, fail=None, delay=0.0) -> None:
        self.summary = summary
        self.fail = fail
        self.delay = delay
        self.folds: list[list] = []
        self.drafts: list[list] = []

    async def draft_reply(self, messages):
        if "running summary" not in messages[0]["content"]:
            self.drafts.append(messages)
            return DRAFT
        self.folds.append(messages)
        await asyncio.sleep(self.delay)
        if self.fail is not None:
            raise self.fail
        return self.summary


def make_user() -> UserData:
    return UserData(
        message_thread_id=7, message_silent_id=None, message_silent_mode=False,
        id=42, full_name="User", username="-", language_code="en",
    )


def draft(storage, provider, summary=None, window=4, text="refund?", data_urls=None, total_timeout=0):
    """Run one draft with ai.summary set to ``summary``; return what the draft request got."""
    from app.config import AIConfig

    ai = AIConfig(
        PROVIDER="openai_compatible", BASE_URL="", API_KEY="k", MODEL="m",
        SYSTEM_PROMPT_PATH="", TIMEOUT_S=5, VISION=False, TOTAL_TIMEOUT_S=total_timeout,
    )
    config = SimpleNamespace(ai=ai, bot=SimpleNamespace(GROUP_ID=-100))
    message = SimpleNamespace(bot=_Bot(), text=text, caption=None)
    raw = {"max_context_messages": window}
    if summary is not None:
        raw["summary"] = summary
    policy = load_policy_from_dict({"ai": raw})
    asyncio.run(run_ai_draft(
        provider, config, message, storage, make_user(), window, ai=policy.ai, data_urls=data_urls,
    ))
    assert storage.draft == DRAFT
    (messages,) = provider.drafts
    return messages


def summary_turn(text):
    return {"role": "system", "content": f"Summary of the earlier part of the conversation:\n{text}"}


def test_summary_is_off_by_default():
    assert AISection().summary.enabled is False
    storage = _Storage(20, summary=(SUMMARY, 2))
    provider = _Provider()

    messages = draft(storage, provider)

    assert messages[1:] == turns(17, 18, 19, 20)
    assert provider.folds == []
    assert storage.stored == []


def test_older_turns_below_the_batch_go_verbatim_after_the_summary():
    storage = _Storage(13, summary=(SUMMARY, 6))
    provider = _Provider()

    messages = draft(storage, provider, {"enabled": True})

    assert messages[0]["role"] == "system"
    assert messages[1] == summary_turn(SUMMARY)
    assert messages[2:] == turns(7, 8, 9, 10, 11, 12, 13)
    assert provider.folds == []
    assert storage.stored == []


def test_without_a_summary_older_turns_go_verbatim():
    storage = _Storage(10)
    provider = _Provider()

    messages = draft(storage, provider, {"enabled": True})

    assert messages[1:] == turns(*range(1, 11))
    assert provider.folds == []


def test_a_full_batch_is_folded_into_the_summary():
    storage = _Storage(14, summary=(SUMMARY, 2))
    provider = _Provider()

    messages = draft(storage, provider, {"enabled": True, "max_chars": 500})

    (fold,) = provider.folds
    assert "at most 500 characters" in fold[0]["content"]
    request = fold[1]["content"]
    assert request.startswith(f"Previous summary:\n{SUMMARY}\n\nNew messages:\n")
    assert request.endswith("\n".join(
        f"{'Customer' if i % 2 else 'Support'}: turn {i}" for i in range(3, 11)
    ))
    assert "turn 2" not in request and "turn 11" not in request
    assert storage.stored == [(NEW_SUMMARY, 10)]
    assert messages[1] == summary_turn(NEW_SUMMARY)
    assert messages[2:] == turns(11, 12, 13, 14)


def test_first_fold_starts_from_no_summary():
    storage = _Storage(7)
    provider = _Provider()

    messages = draft(storage, provider, {"enabled": True, "fold_batch": 3})

    assert provider.folds[0][1]["content"].startswith("Previous summary:\n(none)\n")
    assert storage.stored == [(NEW_SUMMARY, 3)]
    assert messages[1:] == [summary_turn(NEW_SUMMARY), *turns(4, 5, 6, 7)]


@pytest.mark.parametrize("provider", [
    _Provider(fail=RuntimeError("provider is down")),
    _Provider(summary=None),
    _Provider(summary="   "),
])
def test_failed_summary_leaves_the_draft_to_the_window(provider):
    storage = _Storage(14, summary=(SUMMARY, 2))

    messages = draft(storage, provider, {"enabled": True})

    assert len(provider.folds) == 1
    assert messages[1:] == turns(11, 12, 13, 14)
    assert storage.stored == []
    assert storage.summary == (SUMMARY, 2)


def test_summary_runs_under_the_draft_timeout():
    storage = _Storage(14, summary=(SUMMARY, 2))
    provider = _Provider(delay=5)

    # total_timeout_s is the ceiling for the summary request too.
    messages = draft(storage, provider, {"enabled": True}, total_timeout=0.05)

    assert messages[1:] == turns(11, 12, 13, 14)
    assert storage.stored == []


def test_summary_is_cut_to_max_chars():
    storage = _Storage(14)
    provider = _Provider(summary="x" * 300)

    draft(storage, provider, {"enabled": True, "max_chars": 100})

    assert storage.stored == [("x" * 100, 10)]


def test_summary_that_failed_to_store_still_goes_into_the_draft():
    storage = _Storage(14, summary=(SUMMARY, 2), fail_store=True)
    provider = _Provider()

    messages = draft(storage, provider, {"enabled": True})

    assert messages[1:] == [summary_turn(NEW_SUMMARY), *turns(11, 12, 13, 14)]
    assert storage.summary == (SUMMARY, 2)


def test_language_and_images_still_apply_with_a_summary():
    storage = _Storage(13, summary=(SUMMARY, 8))
    provider = _Provider()

    messages = draft(storage, provider, {"enabled": True}, text="turn 13", data_urls=[URL])

    assert messages[0]["content"].endswith("If the user's language is unclear, reply in English.")
    assert messages[1] == summary_turn(SUMMARY)
    assert messages[2:-1] == turns(9, 10, 11, 12)
    assert messages[-1] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "turn 13"},
            {"type": "image_url", "image_url": {"url": URL}},
        ],
    }


def test_summary_options():
    summary = load_policy_from_dict({"ai": {"summary": {"enabled": True}}}).ai.summary
    assert (summary.fold_batch, summary.max_chars) == (8, 1200)

    for bad in ({"fold_batch": 0}, {"max_chars": 10}, {"window": 4}):
        with pytest.raises(ValidationError):
            load_policy_from_dict({"ai": {"summary": bad}})
