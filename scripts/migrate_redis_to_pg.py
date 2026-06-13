"""One-shot migration of the bot-1 user layer from the bundled Redis to PostgreSQL.

Reads the legacy Redis store (``users`` / ``ai_drafts`` / ``conversations:*``) and
writes it into the new PostgreSQL schema:
- ``users``                 — rich support-user records (UserData)
- ``ai_drafts``             — pending AI drafts
- ``conversations``         — rolling transcripts
- ``broadcast_subscribers`` — the broadcast audience (aiogram-broadcast lib)

Usage (from the repo root):
    SRC_REDIS_URL=redis://:pass@host:6379/7 \
    DATABASE_URL=postgresql://user:pass@10.0.0.2:5432/telegram_support \
    python -m scripts.migrate_redis_to_pg

Bot-2 starts empty, so it needs no migration. The source Redis is only read,
never modified; keep it until the PostgreSQL counts are verified.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import asyncpg
from aiogram_broadcast import PostgresBroadcastStorage, Subscriber
from redis.asyncio import Redis

# Allow running as a script from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.bot.utils.redis import RedisStorage, create_schema  # noqa: E402
from app.bot.utils.redis.models import UserData  # noqa: E402

USERS = "users"
DRAFTS = "ai_drafts"
CONV = "conversations"


def _clean_username(username: str | None) -> str | None:
    """Map the bot's '@name' / '-' convention to the lib's bare username/None."""
    if not username or username == "-":
        return None
    return username.lstrip("@") or None


async def main() -> None:
    src_url = os.environ["SRC_REDIS_URL"]
    pg_url = os.environ["DATABASE_URL"]

    redis = Redis.from_url(src_url)
    pool = await asyncpg.create_pool(pg_url)
    await create_schema(pool)
    broadcast = PostgresBroadcastStorage(pool)
    await broadcast.create_schema()
    repo = RedisStorage(pool)

    # --- users (+ broadcast subscribers) ---
    raw_users = await redis.hgetall(USERS)
    migrated = 0
    for raw_id, raw_json in raw_users.items():
        data = json.loads(raw_json)
        known = UserData.__dataclass_fields__.keys()
        user = UserData(**{k: v for k, v in data.items() if k in known})
        await repo.update_user(user.id, user)
        await broadcast.add_subscriber(
            Subscriber(
                id=user.id,
                full_name=user.full_name,
                username=_clean_username(user.username),
                language_code=user.language_code,
            )
        )
        migrated += 1
    print(f"users migrated: {migrated} (redis HLEN={len(raw_users)})")

    # --- ai drafts ---
    raw_drafts = await redis.hgetall(DRAFTS)
    for raw_id, raw_text in raw_drafts.items():
        text = raw_text.decode() if isinstance(raw_text, bytes) else raw_text
        await repo.set_ai_draft(int(raw_id), text)
    print(f"ai_drafts migrated: {len(raw_drafts)}")

    # --- conversations ---
    conv_total = 0
    async for key in redis.scan_iter(match=f"{CONV}:*"):
        key_str = key.decode() if isinstance(key, bytes) else key
        user_id = int(key_str.split(":", 1)[1])
        entries = await redis.lrange(key_str, 0, -1)
        for raw in entries:
            try:
                entry = json.loads(raw)
            except (ValueError, TypeError):
                continue
            await repo.append_conversation(user_id, entry.get("role", ""), entry.get("content", ""))
            conv_total += 1
    print(f"conversation messages migrated: {conv_total}")

    # --- verify ---
    async with pool.acquire() as conn:
        users_count = await conn.fetchval("SELECT count(*) FROM users")
        subs_count = await conn.fetchval("SELECT count(*) FROM broadcast_subscribers")
    print(f"VERIFY: pg users={users_count}, broadcast_subscribers={subs_count}, redis users={len(raw_users)}")
    assert users_count == len(raw_users), "user count mismatch!"

    await redis.aclose()
    await pool.close()
    print("DONE")


if __name__ == "__main__":
    asyncio.run(main())
