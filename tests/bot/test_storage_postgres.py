"""
The storage SQL against a real PostgreSQL server.

Skipped unless TEST_DATABASE_URL points at a disposable database, e.g.
``TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:5432/support``.
Every test works in a schema of its own and drops it afterwards.
"""

import asyncio
import logging
import os
import uuid
from types import SimpleNamespace

import asyncpg
import pytest

from app.bot.policy import load_policy_from_dict
from app.bot.utils import reminders
from app.bot.utils.policy_runtime import run_ai_draft
from app.bot.utils.redis.models import UserData
from app.bot.utils.redis.redis import RedisStorage, create_schema

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not set")

# The schema before first_messages and the draft log: what an upgrade starts from.
LEGACY_SCHEMA = [
    """
    CREATE TABLE users (
        id BIGINT PRIMARY KEY,
        message_thread_id BIGINT,
        message_silent_id BIGINT,
        message_silent_mode BOOLEAN NOT NULL DEFAULT FALSE,
        full_name TEXT NOT NULL DEFAULT '',
        username TEXT,
        state TEXT NOT NULL DEFAULT 'member',
        is_banned BOOLEAN NOT NULL DEFAULT FALSE,
        language_code TEXT,
        created_at TEXT,
        status TEXT NOT NULL DEFAULT 'open'
    )
    """,
    "CREATE UNIQUE INDEX users_thread_idx ON users (message_thread_id) WHERE message_thread_id IS NOT NULL",
    "CREATE TABLE ai_drafts (user_id BIGINT PRIMARY KEY, text TEXT NOT NULL)",
    """
    CREATE TABLE conversations (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX conversations_user_idx ON conversations (user_id, id)",
]


def run(scenario, legacy: bool = False) -> None:
    """Run ``scenario(pool, storage)`` in a fresh schema, new or legacy."""

    async def main():
        schema = f"test_{uuid.uuid4().hex}"
        conn = await asyncpg.connect(DSN)
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        await conn.close()
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2, server_settings={"search_path": schema})
        try:
            if legacy:
                async with pool.acquire() as conn:
                    for statement in LEGACY_SCHEMA:
                        await conn.execute(statement)
            else:
                await create_schema(pool)
            await scenario(pool, RedisStorage(pool))
        finally:
            async with pool.acquire() as conn:
                await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
            await pool.close()

    asyncio.run(main())


def user(id_: int, thread_id: int | None = None) -> UserData:
    return UserData(
        message_thread_id=thread_id, message_silent_id=None, message_silent_mode=False,
        id=id_, full_name=f"User {id_}", username="-", language_code="en",
    )


async def first_messages(pool) -> set[int]:
    async with pool.acquire() as conn:
        return {row["user_id"] for row in await conn.fetch("SELECT user_id FROM first_messages")}


def test_upgrade_backfills_every_user_who_wrote():
    async def scenario(pool, storage):
        for id_ in (1, 2, 3, 4, 5):
            await storage.update_user(id_, user(id_, thread_id=100 + id_))
        await storage.append_conversation(1, "user", "my payout is late")
        await storage.append_conversation(1, "assistant", "Payouts go out on Fridays.")
        # Media without a caption leaves no user turn, only the manager's answer.
        await storage.append_conversation(2, "assistant", "Got your screenshot, checking.")
        # A screenshot with a draft still waiting for the manager.
        await storage.set_ai_draft(3, "Looks like a failed payout.")
        # 4 only pressed /start: a topic, but nothing written.
        # 5 wrote long ago; the manager's later turns pushed theirs out.
        await storage.append_conversation(5, "user", "hello")
        for n in range(RedisStorage.CONV_MAX):
            await storage.append_conversation(5, "assistant", f"note {n}")

        await create_schema(pool)
        await create_schema(pool)

        assert await first_messages(pool) == {1, 2, 3, 5}
        assert await storage.claim_first_message(4) is True
        assert await storage.claim_first_message(1) is False
        # Old rows load with the new column in place.
        assert (await storage.get_user(1)).message_thread_id == 101
        assert await storage.get_user_category(1) is None

    run(scenario, legacy=True)


def test_backfill_runs_only_when_the_table_is_created():
    async def scenario(pool, storage):
        await storage.update_user(6, user(6, thread_id=106))
        # A first message dropped by policy is still in the transcript.
        await storage.append_conversation(6, "user", "spam")
        assert await storage.claim_first_message(6) is True
        await storage.release_first_message(6)

        await create_schema(pool)

        assert await first_messages(pool) == set()
        assert await storage.claim_first_message(6) is True

    run(scenario)


def test_claims():
    async def scenario(pool, storage):
        assert await storage.claim_first_message(7) is True
        assert await storage.claim_first_message(7) is False
        await storage.release_first_message(7)
        await storage.release_first_message(8)
        assert await storage.claim_first_message(7) is True

        assert await storage.claim_auto_reply(7, "received") is True
        assert await storage.claim_auto_reply(7, "received") is False
        assert await storage.claim_auto_reply(7, "rules") is True
        assert await storage.claim_auto_reply(8, "received") is True

    run(scenario)


def test_users_and_category():
    async def scenario(pool, storage):
        await storage.update_user(9, user(9))
        await storage.set_user_category(9, "payout")
        # A handler holding an older UserData does not wipe the category.
        await storage.update_user(9, user(9, thread_id=109))
        assert await storage.get_user_category(9) == "payout"
        assert (await storage.get_by_message_thread_id(109)).id == 9
        assert await storage.get_all_users_ids() == [9]

        # No user row, nothing to update.
        await storage.set_user_category(10, "payout")
        assert await storage.get_user_category(10) is None
        assert await storage.get_user(10) is None

        await storage.append_conversation(9, "user", "  hi  ")
        await storage.append_conversation(9, "user", "   ")
        await storage.append_conversation(9, "assistant", "hello")
        assert await storage.get_conversation(9, 12) == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

    run(scenario)


def test_draft_log_and_stats():
    async def scenario(pool, storage):
        await storage.log_draft(11, 111, "payout", "first draft")
        await storage.log_draft(11, 111, "payout", "second draft")
        await storage.resolve_draft(11, "sent")
        # Nothing pending any more.
        await storage.resolve_draft(11, "skipped")
        await storage.log_draft(12, 112, None, "draft")
        await storage.resolve_draft(12, "manager_replied")
        await storage.log_draft(13, 113, "payout", "pending draft")
        await storage.log_draft(13, 113, "payout", "auto", outcome="auto_sent")
        await storage.log_draft(14, 114, "other", "old draft")
        await storage.resolve_draft(14, "skipped")

        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT user_id, text, outcome, outcome_at FROM ai_draft_log ORDER BY id")
            await conn.execute("UPDATE ai_draft_log SET created_at = now() - interval '30 days' WHERE user_id = 14")
        assert [(r["user_id"], r["outcome"]) for r in rows] == [
            (11, "superseded"), (11, "sent"), (12, "manager_replied"),
            (13, "superseded"), (13, "auto_sent"), (14, "skipped"),
        ]
        assert all(r["outcome_at"] is not None for r in rows)

        def counts(total, sent=0, skipped=0, manager_replied=0, auto_sent=0):
            return {"total": total, "sent": sent, "skipped": skipped,
                    "manager_replied": manager_replied, "auto_sent": auto_sent}

        recent = {
            "payout": counts(4, sent=1, auto_sent=1),
            None: counts(1, manager_replied=1),
        }
        assert await storage.get_draft_stats(7) == recent
        assert await storage.get_draft_stats(3650) == {**recent, "other": counts(1, skipped=1)}
        assert await storage.get_draft_stats() == await storage.get_draft_stats(3650)

    run(scenario)


def test_pending_draft_has_no_outcome_time():
    async def scenario(pool, storage):
        await storage.log_draft(15, None, None, "draft")
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT outcome, outcome_at FROM ai_draft_log")
        assert (row["outcome"], row["outcome_at"]) == ("pending", None)

    run(scenario)


def test_auto_modes():
    async def scenario(pool, storage):
        assert await storage.get_auto_modes() == {}
        assert await storage.get_auto_mode("payout") is False

        await storage.set_auto_mode("payout", True, 1)
        await storage.set_auto_mode("other", False, 1)
        assert await storage.get_auto_mode("payout") is True
        await storage.set_auto_mode("payout", False, 2)

        assert await storage.get_auto_modes() == {"payout": False, "other": False}
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT updated_by FROM ai_auto_modes WHERE category = 'payout'") == 2

    run(scenario)


def test_upgrade_starts_with_no_reply_waits():
    async def scenario(pool, storage):
        await storage.update_user(16, user(16, thread_id=116))
        await storage.append_conversation(16, "user", "still waiting")

        await create_schema(pool)
        await create_schema(pool)

        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM reply_waits") == 0
        assert await storage.get_due_reply_waits([1], []) == []

    run(scenario, legacy=True)


def test_conversation_summary():
    async def scenario(pool, storage):
        assert await storage.get_conversation_summary(16) is None

        await storage.set_conversation_summary(16, "wants a refund", 5)
        # A fold that covers no more than the stored one does not replace it.
        await storage.set_conversation_summary(16, "stale fold", 3)
        await storage.set_conversation_summary(16, "stale fold", 5)
        assert await storage.get_conversation_summary(16) == ("wants a refund", 5)

        await storage.set_conversation_summary(16, "refund promised by Friday", 9)
        assert await storage.get_conversation_summary(16) == ("refund promised by Friday", 9)
        assert await storage.get_conversation_summary(17) is None

    run(scenario)


def test_upgrade_adds_the_summary_table():
    async def scenario(pool, storage):
        await create_schema(pool)
        await create_schema(pool)
        await storage.set_conversation_summary(18, "said hello", 1)
        assert await storage.get_conversation_summary(18) == ("said hello", 1)

    run(scenario, legacy=True)


async def wait_since(pool, user_id: int, minutes: int, reminded: int = 0) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reply_waits SET since = now() - make_interval(mins => $2), reminded = $3 "
            "WHERE user_id = $1",
            user_id, minutes, reminded,
        )


def test_reply_waits():
    async def scenario(pool, storage):
        await storage.update_user(17, user(17, thread_id=117))
        await storage.start_reply_wait(17)
        await wait_since(pool, 17, 200)
        # A second message does not move the start of the wait.
        await storage.start_reply_wait(17)
        (due,) = await storage.get_due_reply_waits([180, 1440], [])
        assert (due["user_id"], due["message_thread_id"], due["language_code"]) == (17, 117, "en")
        assert (due["reminded"], due["level"], due["waited_minutes"]) == (0, 1, 200)

        assert await storage.claim_reply_reminder(17, due["since"], 1) is True
        assert await storage.claim_reply_reminder(17, due["since"], 1) is False
        assert await storage.get_due_reply_waits([180, 1440], []) == []

        # A reply ends the wait; the next message starts a new one.
        await storage.end_reply_wait(17)
        await storage.end_reply_wait(17)
        assert await storage.claim_reply_reminder(17, due["since"], 2) is False
        await storage.start_reply_wait(17)
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT since, reminded FROM reply_waits WHERE user_id = 17")
        assert row["reminded"] == 0 and row["since"] > due["since"]

    run(scenario)


def test_conversation_since():
    async def scenario(pool, storage):
        for n in range(10):
            await storage.append_conversation(19, "assistant" if n % 2 else "user", f"turn {n}")
        await storage.append_conversation(20, "user", "someone else")

        rows = await storage.get_conversation_since(19, 0, 4)
        ids = [row["id"] for row in rows]
        assert [row["content"] for row in rows] == [f"turn {n}" for n in range(10)]
        assert rows[1] == {"id": ids[1], "role": "assistant", "content": "turn 1"}
        assert ids == sorted(ids)

        # The messages above the covered id, then the window.
        rows = await storage.get_conversation_since(19, ids[5], 2)
        assert [row["content"] for row in rows] == ["turn 6", "turn 7", "turn 8", "turn 9"]
        # The window comes whole even when part of it is covered.
        rows = await storage.get_conversation_since(19, ids[8], 4)
        assert [row["content"] for row in rows] == ["turn 6", "turn 7", "turn 8", "turn 9"]
        assert await storage.get_conversation_since(21, 0, 4) == []

    run(scenario)


def test_released_reminder_is_due_again():
    async def scenario(pool, storage):
        await storage.update_user(18, user(18, thread_id=118))
        await storage.start_reply_wait(18)
        await wait_since(pool, 18, 1500, reminded=1)
        (due,) = await storage.get_due_reply_waits([180, 1440], [])
        assert await storage.claim_reply_reminder(18, due["since"], 2) is True

        await storage.release_reply_reminder(18, due["since"], 2, 1)
        (again,) = await storage.get_due_reply_waits([180, 1440], [])
        assert (again["reminded"], again["level"]) == (1, 2)

        # A release after the wait ended or moved on changes nothing.
        await storage.end_reply_wait(18)
        await storage.start_reply_wait(18)
        await storage.release_reply_reminder(18, due["since"], 0, 1)
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT reminded FROM reply_waits WHERE user_id = 18") == 0

    run(scenario)


def test_waits_left_from_before_reminders_were_off_are_dropped():
    async def scenario(pool, storage):
        for id_ in (19, 20):
            await storage.update_user(id_, user(id_, thread_id=100 + id_))
            await storage.start_reply_wait(id_)
            await wait_since(pool, id_, 1500)

        # A start with reminders off; managers answer meanwhile, untracked.
        await reminders.drop_reply_waits(pool)
        await reminders.drop_reply_waits(pool)
        # Back on: no reminders about those conversations, new messages count.
        assert await storage.get_due_reply_waits([180, 1440], []) == []
        await storage.start_reply_wait(19)
        await wait_since(pool, 19, 200)
        assert [row["user_id"] for row in await storage.get_due_reply_waits([180, 1440], [])] == [19]

    run(scenario)


def test_due_reply_waits_skip_what_needs_no_reminder():
    async def scenario(pool, storage):
        def make(id_, thread=True, **fields):
            data = user(id_, thread_id=100 + id_ if thread else None)
            for key, value in fields.items():
                setattr(data, key, value)
            return data

        users = {
            21: make(21),                                # due: first threshold
            22: make(22),                                # too early
            23: make(23, is_banned=True),
            24: make(24, message_silent_mode=True),
            25: make(25, status="closed"),
            26: make(26, thread=False),
            27: make(27),                                # skipped category
            28: make(28, status="escalated"),            # due, other category
            29: make(29),                                # due: second threshold
            30: make(30),                                # every threshold reminded
            31: make(31),                                # both thresholds at once
        }
        minutes = {21: 200, 22: 30, 29: 1500, 30: 1500, 31: 1500}
        reminded = {29: 1, 30: 2}
        for id_, data in users.items():
            await storage.update_user(id_, data)
            await storage.start_reply_wait(id_)
            await wait_since(pool, id_, minutes.get(id_, 200), reminded.get(id_, 0))
        await storage.set_user_category(27, "spam")
        await storage.set_user_category(28, "payout")
        # A wait of a user without a record is ignored.
        await storage.start_reply_wait(32)
        await wait_since(pool, 32, 200)

        due = await storage.get_due_reply_waits([180, 1440], ["spam"])

        assert {row["user_id"]: (row["reminded"], row["level"]) for row in due} == {
            21: (0, 1), 28: (0, 1), 29: (1, 2), 31: (0, 2),
        }
        assert {row["user_id"] for row in await storage.get_due_reply_waits([180, 1440], [])} == {
            21, 27, 28, 29, 31,
        }
        assert await storage.get_due_reply_waits([], []) == []

    run(scenario)


def test_trim_keeps_messages_not_in_the_summary():
    async def scenario(pool, storage):
        keeping = RedisStorage(pool, keep_unsummarized=True)
        total = RedisStorage.CONV_MAX + 5

        async def contents(user_id):
            return [row["content"] for row in await keeping.get_conversation_since(user_id, 0, 0)]

        # No summary yet: nothing is folded, nothing goes.
        for n in range(total):
            await keeping.append_conversation(22, "user", f"turn {n}")
        assert len(await contents(22)) == total

        # The first 3 folded: they go, the unfolded ones beyond CONV_MAX stay.
        rows = await keeping.get_conversation_since(22, 0, 0)
        await keeping.set_conversation_summary(22, "summary", rows[2]["id"])
        await keeping.append_conversation(22, "user", "one more")
        assert await contents(22) == [f"turn {n}" for n in range(3, total)] + ["one more"]

        # Everything folded: back to the last CONV_MAX.
        rows = await keeping.get_conversation_since(22, 0, 0)
        await keeping.set_conversation_summary(22, "summary", rows[-1]["id"])
        await keeping.append_conversation(22, "user", "and another")
        assert len(await contents(22)) == RedisStorage.CONV_MAX

        # Without the flag the trim ignores the summary, as before.
        await storage.set_conversation_summary(23, "summary", 1)
        for n in range(total):
            await storage.append_conversation(23, "user", f"turn {n}")
        assert await contents(23) == [f"turn {n}" for n in range(5, total)]

    run(scenario)


def test_trim_hard_cap_drops_even_unsummarized_messages(caplog):
    async def scenario(pool, storage):
        keeping = RedisStorage(pool, keep_unsummarized=True)
        cap = RedisStorage.CONV_HARD_MAX

        async def contents(user_id):
            return [row["content"] for row in await keeping.get_conversation_since(user_id, 0, 0)]

        # Nothing folded: the oldest go past the cap, each one with a warning.
        with caplog.at_level(logging.WARNING, logger="app.bot.utils.redis.redis"):
            for n in range(cap + 3):
                await keeping.append_conversation(25, "user", f"turn {n}")
        assert await contents(25) == [f"turn {n}" for n in range(3, cap + 3)]
        lost = [r.getMessage() for r in caplog.records if "before the summary" in r.getMessage()]
        assert len(lost) == 3
        assert all(f"user 25 is over {cap} messages: 1 dropped" in m for m in lost)

        # Dropping messages already in the summary is no loss: no warning.
        caplog.clear()
        rows = await keeping.get_conversation_since(25, 0, 0)
        await keeping.set_conversation_summary(25, "summary", rows[1]["id"])
        with caplog.at_level(logging.WARNING, logger="app.bot.utils.redis.redis"):
            await keeping.append_conversation(25, "user", "one more")
        assert await contents(25) == [f"turn {n}" for n in range(5, cap + 3)] + ["one more"]
        assert caplog.records == []

    run(scenario)


class _FoldingProvider:
    """Answers the n-th summary request with "summary n"."""

    def __init__(self) -> None:
        self.folds: list[str] = []
        self.drafts: list[list] = []

    async def draft_reply(self, messages):
        if "running summary" not in messages[0]["content"]:
            self.drafts.append(messages)
            return "draft"
        self.folds.append(messages[1]["content"])
        return f"summary {len(self.folds)}"


async def draft_with_summary(storage, provider, user_id):
    from app.config import AIConfig

    ai = AIConfig(
        PROVIDER="openai_compatible", BASE_URL="", API_KEY="k", MODEL="m",
        SYSTEM_PROMPT_PATH="", TIMEOUT_S=5, VISION=False,
    )
    bot = SimpleNamespace(send_message=lambda **kwargs: asyncio.sleep(0))
    config = SimpleNamespace(ai=ai, bot=SimpleNamespace(GROUP_ID=-100))
    message = SimpleNamespace(bot=bot, text="", caption=None)
    policy = load_policy_from_dict({"ai": {"summary": {"enabled": True}}})
    await run_ai_draft(provider, config, message, storage, user(user_id, thread_id=100 + user_id), 12,
                       ai=policy.ai, data_urls=[])


def test_backlog_is_folded_in_portions():
    async def scenario(pool, storage):
        keeping = RedisStorage(pool, keep_unsummarized=True)
        # 140 turns older than the 12-turn window: portions of 40, 40, 40 and 20.
        for n in range(152):
            await keeping.append_conversation(27, "user", f"turn {n}")
        ids = [row["id"] for row in await keeping.get_conversation_since(27, 0, 0)]
        provider = _FoldingProvider()

        await draft_with_summary(keeping, provider, 27)

        assert len(provider.folds) == 4
        assert provider.folds[0].endswith("Customer: turn 39")
        assert provider.folds[3].startswith("Previous summary:\nsummary 3\n")
        assert provider.folds[3].endswith("Customer: turn 139")
        assert await keeping.get_conversation_summary(27) == ("summary 4", ids[139])
        (messages,) = provider.drafts
        assert messages[1]["content"].endswith("summary 4")
        assert [m["content"] for m in messages[2:]] == [f"turn {n}" for n in range(140, 152)]

        # Folded now, so the next message trims the transcript back to CONV_MAX.
        await keeping.append_conversation(27, "user", "one more")
        assert len(await keeping.get_conversation_since(27, 0, 0)) == RedisStorage.CONV_MAX

    run(scenario)
