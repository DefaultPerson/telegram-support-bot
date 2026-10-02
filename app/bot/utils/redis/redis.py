"""User-layer storage.

Despite the legacy name ``RedisStorage`` (and the ``utils.redis`` package
path, kept for import stability), this is now backed by **PostgreSQL** via
asyncpg. Redis is used only for aiogram FSM and the apscheduler job store.

Tables (created idempotently by :func:`create_schema`):
- ``users``         — rich support-user records (``UserData``); the
  ``message_thread_id`` unique index replaces the old ``users_index_*`` hashes.
- ``ai_drafts``     — pending AI draft reply per user.
- ``conversations`` — rolling per-user transcript (trimmed to ``CONV_MAX``).
- ``auto_replies_sent`` — once-only policy auto-replies already sent per user.
- ``first_messages`` — users who have already sent their first message.
- ``ai_draft_log``  — every AI draft and its outcome (``ai.log_drafts``).
- ``ai_auto_modes`` — categories whose drafts go to the user without review.
- ``reply_waits``   — users waiting for a reply since ``since``, with the number
  of reminders already posted (``reminders`` in the policy).

``users.category`` holds the category picked for the user's first message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import UserData

if TYPE_CHECKING:
    from asyncpg import Pool, Record


async def create_schema(pool: Pool) -> None:
    """Create the user-layer tables and indexes if they do not exist."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
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
            """
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS users_thread_idx "
            "ON users (message_thread_id) WHERE message_thread_id IS NOT NULL"
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_drafts (
                user_id BIGINT PRIMARY KEY,
                text TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS conversations_user_idx ON conversations (user_id, id)"
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auto_replies_sent (
                user_id BIGINT NOT NULL,
                template_key TEXT NOT NULL,
                sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (user_id, template_key)
            )
            """
        )
        # Users who wrote before this table existed are not new. Filled once, as
        # the table is created: from then on the bot keeps it itself, and a
        # transcript row alone no longer proves the user reached the topic.
        async with conn.transaction():
            backfill = await conn.fetchval("SELECT to_regclass('first_messages') IS NULL")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS first_messages (
                    user_id BIGINT PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            if backfill:
                # Any transcript row counts, not only the user's own turns:
                # media without a caption leaves none, and old turns are trimmed
                # to CONV_MAX. A pending draft also means the user wrote.
                await conn.execute(
                    "INSERT INTO first_messages (user_id) "
                    "SELECT user_id FROM conversations UNION SELECT user_id FROM ai_drafts "
                    "ON CONFLICT DO NOTHING"
                )
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS category TEXT")
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_draft_log (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                thread_id BIGINT,
                category TEXT,
                text TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                outcome TEXT NOT NULL DEFAULT 'pending',
                outcome_at TIMESTAMPTZ
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS ai_draft_log_pending_idx "
            "ON ai_draft_log (user_id) WHERE outcome = 'pending'"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS ai_draft_log_created_idx ON ai_draft_log (created_at)"
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_auto_modes (
                category TEXT PRIMARY KEY,
                enabled BOOLEAN NOT NULL DEFAULT FALSE,
                updated_by BIGINT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # Not backfilled: only messages written after the upgrade start a wait,
        # so turning reminders on does not flood the group with old conversations.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reply_waits (
                user_id BIGINT PRIMARY KEY,
                since TIMESTAMPTZ NOT NULL DEFAULT now(),
                reminded SMALLINT NOT NULL DEFAULT 0
            )
            """
        )


class RedisStorage:
    """Repository for support-user data (PostgreSQL-backed; legacy name)."""

    CONV_MAX = 40

    def __init__(self, pool: Pool) -> None:
        """
        :param pool: asyncpg connection pool.
        """
        self.pool = pool

    @staticmethod
    def _row_to_user(row: Record) -> UserData:
        """Build a UserData from a database row."""
        return UserData(
            message_thread_id=row["message_thread_id"],
            message_silent_id=row["message_silent_id"],
            message_silent_mode=row["message_silent_mode"],
            id=row["id"],
            full_name=row["full_name"],
            username=row["username"],
            state=row["state"],
            is_banned=row["is_banned"],
            language_code=row["language_code"],
            created_at=row["created_at"],
            status=row["status"],
        )

    async def get_by_message_thread_id(self, message_thread_id: int) -> UserData | None:
        """Retrieve user data based on message thread ID."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE message_thread_id = $1",
                message_thread_id,
            )
        return None if row is None else self._row_to_user(row)

    async def get_user(self, id_: int) -> UserData | None:
        """Retrieve user data based on user ID."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", id_)
        return None if row is None else self._row_to_user(row)

    async def update_user(self, id_: int, data: UserData) -> None:
        """Insert or update user data (upsert by id)."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (
                    id, message_thread_id, message_silent_id, message_silent_mode,
                    full_name, username, state, is_banned, language_code, created_at, status
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (id) DO UPDATE SET
                    message_thread_id = EXCLUDED.message_thread_id,
                    message_silent_id = EXCLUDED.message_silent_id,
                    message_silent_mode = EXCLUDED.message_silent_mode,
                    full_name = EXCLUDED.full_name,
                    username = EXCLUDED.username,
                    state = EXCLUDED.state,
                    is_banned = EXCLUDED.is_banned,
                    language_code = EXCLUDED.language_code,
                    created_at = EXCLUDED.created_at,
                    status = EXCLUDED.status
                """,
                id_,
                data.message_thread_id,
                data.message_silent_id,
                data.message_silent_mode,
                data.full_name,
                data.username,
                data.state,
                data.is_banned,
                data.language_code,
                data.created_at,
                data.status,
            )

    async def get_all_users_ids(self) -> list[int]:
        """Retrieve all user IDs."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT id FROM users")
        return [int(row["id"]) for row in rows]

    async def set_ai_draft(self, user_id: int, text: str) -> None:
        """Store a pending AI draft reply for the given user."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO ai_drafts (user_id, text) VALUES ($1, $2) "
                "ON CONFLICT (user_id) DO UPDATE SET text = EXCLUDED.text",
                user_id,
                text,
            )

    async def get_ai_draft(self, user_id: int) -> str | None:
        """Retrieve the pending AI draft reply for the given user, if any."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT text FROM ai_drafts WHERE user_id = $1", user_id
            )

    async def clear_ai_draft(self, user_id: int) -> None:
        """Remove the pending AI draft reply for the given user."""
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM ai_drafts WHERE user_id = $1", user_id)

    async def claim_first_message(self, user_id: int) -> bool:
        """Record that the user has written; True only for their very first message."""
        async with self.pool.acquire() as conn:
            inserted = await conn.fetchval(
                "INSERT INTO first_messages (user_id) VALUES ($1) "
                "ON CONFLICT DO NOTHING RETURNING user_id",
                user_id,
            )
        return inserted is not None

    async def release_first_message(self, user_id: int) -> None:
        """Undo :meth:`claim_first_message`: the user's next message counts as the first one."""
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM first_messages WHERE user_id = $1", user_id)

    async def claim_auto_reply(self, user_id: int, template_key: str) -> bool:
        """Mark a once-only auto-reply as sent; True only the first time per user and key."""
        async with self.pool.acquire() as conn:
            inserted = await conn.fetchval(
                "INSERT INTO auto_replies_sent (user_id, template_key) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING RETURNING user_id",
                user_id,
                template_key,
            )
        return inserted is not None

    async def append_conversation(self, user_id: int, role: str, text: str) -> None:
        """Append a message to the rolling conversation transcript for a user."""
        text = (text or "").strip()
        if not text:
            return
        text = text[:2000]
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO conversations (user_id, role, content) VALUES ($1, $2, $3)",
                user_id,
                role,
                text,
            )
            # Trim to the last CONV_MAX messages for this user.
            await conn.execute(
                """
                DELETE FROM conversations
                WHERE user_id = $1 AND id NOT IN (
                    SELECT id FROM conversations WHERE user_id = $1
                    ORDER BY id DESC LIMIT $2
                )
                """,
                user_id,
                self.CONV_MAX,
            )

    async def get_conversation(self, user_id: int, limit: int) -> list[dict]:
        """Return the last ``limit`` messages of the conversation in chronological order."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT role, content FROM conversations WHERE user_id = $1 "
                "ORDER BY id DESC LIMIT $2",
                user_id,
                limit,
            )
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    async def get_user_category(self, user_id: int) -> str | None:
        """Return the category picked for the user's first message, if any."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT category FROM users WHERE id = $1", user_id)

    async def set_user_category(self, user_id: int, category: str) -> None:
        """
        Store the user's category. Kept out of :meth:`update_user`, so a handler
        holding an older UserData cannot wipe it.
        """
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE users SET category = $2 WHERE id = $1", user_id, category)

    async def log_draft(
        self,
        user_id: int,
        thread_id: int | None,
        category: str | None,
        text: str,
        outcome: str = "pending",
    ) -> None:
        """Record a draft; a still pending earlier draft of the user becomes ``superseded``."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE ai_draft_log SET outcome = 'superseded', outcome_at = now() "
                    "WHERE user_id = $1 AND outcome = 'pending'",
                    user_id,
                )
                await conn.execute(
                    "INSERT INTO ai_draft_log (user_id, thread_id, category, text, outcome, outcome_at) "
                    "VALUES ($1, $2, $3, $4, $5::text, CASE WHEN $5::text = 'pending' THEN NULL ELSE now() END)",
                    user_id,
                    thread_id,
                    category,
                    text,
                    outcome,
                )

    async def resolve_draft(self, user_id: int, outcome: str) -> None:
        """Set the outcome of the user's pending draft (sent, skipped, manager_replied)."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE ai_draft_log SET outcome = $2, outcome_at = now() "
                "WHERE user_id = $1 AND outcome = 'pending'",
                user_id,
                outcome,
            )

    async def get_draft_stats(self, days: int | None = None) -> dict[str | None, dict[str, int]]:
        """Count drafts per category and outcome, optionally over the last ``days`` days."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT category,
                       count(*) AS total,
                       count(*) FILTER (WHERE outcome = 'sent') AS sent,
                       count(*) FILTER (WHERE outcome = 'skipped') AS skipped,
                       count(*) FILTER (WHERE outcome = 'manager_replied') AS manager_replied,
                       count(*) FILTER (WHERE outcome = 'auto_sent') AS auto_sent
                FROM ai_draft_log
                WHERE $1::int IS NULL OR created_at >= now() - make_interval(days => $1::int)
                GROUP BY category
                """,
                days,
            )
        return {
            row["category"]: {
                key: int(row[key])
                for key in ("total", "sent", "skipped", "manager_replied", "auto_sent")
            }
            for row in rows
        }

    async def get_auto_modes(self) -> dict[str, bool]:
        """Return the auto-reply mode of every category that has one stored."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT category, enabled FROM ai_auto_modes")
        return {row["category"]: row["enabled"] for row in rows}

    async def get_auto_mode(self, category: str) -> bool:
        """True when drafts of this category go to the user without review."""
        async with self.pool.acquire() as conn:
            enabled = await conn.fetchval(
                "SELECT enabled FROM ai_auto_modes WHERE category = $1", category
            )
        return bool(enabled)

    async def set_auto_mode(self, category: str, enabled: bool, updated_by: int) -> None:
        """Switch automatic replies for a category on or off."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO ai_auto_modes (category, enabled, updated_by) VALUES ($1, $2, $3) "
                "ON CONFLICT (category) DO UPDATE SET enabled = EXCLUDED.enabled, "
                "updated_by = EXCLUDED.updated_by, updated_at = now()",
                category,
                enabled,
                updated_by,
            )

    async def start_reply_wait(self, user_id: int) -> None:
        """The user wrote: start waiting for a reply, unless already waiting."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO reply_waits (user_id) VALUES ($1) ON CONFLICT DO NOTHING",
                user_id,
            )

    async def end_reply_wait(self, user_id: int) -> None:
        """The user got a reply: the wait and its reminders start over."""
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM reply_waits WHERE user_id = $1", user_id)

    async def get_due_reply_waits(
        self, after_minutes: list[int], skip_categories: list[str]
    ) -> list[dict]:
        """
        Waits that passed a threshold they were not reminded of yet. ``level``
        is the number of thresholds passed so far. Banned and silenced users,
        closed or missing topics and skipped categories are left out.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM (
                    SELECT w.user_id, w.since, w.reminded,
                           u.message_thread_id, u.language_code,
                           extract(epoch FROM now() - w.since)::bigint / 60 AS waited_minutes,
                           (SELECT count(*) FROM unnest($1::int[]) AS t(minutes)
                            WHERE w.since <= now() - make_interval(mins => t.minutes))::int AS level
                    FROM reply_waits w
                    JOIN users u ON u.id = w.user_id
                    WHERE NOT u.is_banned
                      AND NOT u.message_silent_mode
                      AND u.status <> 'closed'
                      AND u.message_thread_id IS NOT NULL
                      AND (u.category IS NULL OR u.category <> ALL($2::text[]))
                ) due
                WHERE level > reminded
                ORDER BY since
                """,
                after_minutes,
                skip_categories,
            )
        return [dict(row) for row in rows]

    async def claim_reply_reminder(self, user_id: int, since, level: int) -> bool:
        """
        Record that the reminders up to ``level`` were posted for this wait.
        False when another check got there first or the wait has ended since.
        """
        async with self.pool.acquire() as conn:
            claimed = await conn.fetchval(
                "UPDATE reply_waits SET reminded = $3 "
                "WHERE user_id = $1 AND since = $2 AND reminded < $3 RETURNING user_id",
                user_id,
                since,
                level,
            )
        return claimed is not None
