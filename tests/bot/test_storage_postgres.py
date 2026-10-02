"""
The storage SQL against a real PostgreSQL server.

Skipped unless TEST_DATABASE_URL points at a disposable database, e.g.
``TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:5432/support``.
Every test works in a schema of its own and drops it afterwards.
"""

import asyncio
import os
import uuid

import asyncpg
import pytest

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
