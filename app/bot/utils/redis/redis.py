import json

from redis.asyncio import Redis

from .models import UserData


class RedisStorage:
    """Class for managing user data storage using Redis."""

    NAME = "users"
    DRAFTS = "ai_drafts"
    CONV = "conversations"
    CONV_MAX = 40

    def __init__(self, redis: Redis) -> None:
        """
        Initializes the RedisStorage instance.

        :param redis: The Redis instance to be used for data storage.
        """
        self.redis = redis

    async def _get(self, name: str, key: str | int) -> bytes | None:
        """
        Retrieves data from Redis.

        :param name: The name of the Redis hash.
        :param key: The key to be retrieved.
        :return: The retrieved data or None if not found.
        """
        async with self.redis.client() as client:
            return await client.hget(name, key)

    async def _set(self, name: str, key: str | int, value: any) -> None:
        """
        Sets data in Redis.

        :param name: The name of the Redis hash.
        :param key: The key to be set.
        :param value: The value to be set.
        """
        async with self.redis.client() as client:
            await client.hset(name, key, value)

    async def _update_index(self, message_thread_id: int, user_id: int) -> None:
        """
        Updates the user index in Redis.

        :param message_thread_id: The ID of the message thread.
        :param user_id: The ID of the user to be updated in the index.
        """
        index_key = f"{self.NAME}_index_{message_thread_id}"
        await self._set(index_key, user_id, "1")

    async def get_by_message_thread_id(self, message_thread_id: int) -> UserData | None:
        """
        Retrieves user data based on message thread ID.

        :param message_thread_id: The ID of the message thread.
        :return: The user data or None if not found.
        """
        user_id = await self._get_user_id_by_message_thread_id(message_thread_id)
        return None if user_id is None else await self.get_user(user_id)

    async def _get_user_id_by_message_thread_id(self, message_thread_id: int) -> int | None:
        """
        Retrieves user ID based on message thread ID.

        :param message_thread_id: The ID of the message thread.
        :return: The user ID or None if not found.
        """
        index_key = f"{self.NAME}_index_{message_thread_id}"
        async with self.redis.client() as client:
            user_ids = await client.hkeys(index_key)
            return int(user_ids[0]) if user_ids else None

    async def get_user(self, id_: int) -> UserData | None:
        """
        Retrieves user data based on user ID.

        :param id_: The ID of the user.
        :return: The user data or None if not found.
        """
        data = await self._get(self.NAME, id_)
        if data is not None:
            decoded_data = json.loads(data)
            # Drop unknown keys so records written by other bot versions still load.
            known = UserData.__dataclass_fields__.keys()
            return UserData(**{k: v for k, v in decoded_data.items() if k in known})
        return None

    async def update_user(self, id_: int, data: UserData) -> None:
        """
        Updates user data in Redis.

        :param id_: The ID of the user to be updated.
        :param data: The updated user data.
        """
        json_data = json.dumps(data.to_dict())
        await self._set(self.NAME, id_, json_data)
        await self._update_index(data.message_thread_id, id_)

    async def get_all_users_ids(self) -> list[int]:
        """
        Retrieves all user IDs stored in the Redis hash.

        :return: A list of all user IDs.
        """
        async with self.redis.client() as client:
            user_ids = await client.hkeys(self.NAME)
            return [int(user_id) for user_id in user_ids]

    async def set_ai_draft(self, user_id: int, text: str) -> None:
        """Stores a pending AI draft reply for the given user."""
        await self._set(self.DRAFTS, user_id, text)

    async def get_ai_draft(self, user_id: int) -> str | None:
        """Retrieves the pending AI draft reply for the given user, if any."""
        value = await self._get(self.DRAFTS, user_id)
        return value.decode() if isinstance(value, bytes) else value

    async def clear_ai_draft(self, user_id: int) -> None:
        """Removes the pending AI draft reply for the given user."""
        async with self.redis.client() as client:
            await client.hdel(self.DRAFTS, user_id)

    async def append_conversation(self, user_id: int, role: str, text: str) -> None:
        """Append a message to the rolling conversation transcript for a user."""
        text = (text or "").strip()
        if not text:
            return
        text = text[:2000]
        key = f"{self.CONV}:{user_id}"
        entry = json.dumps({"role": role, "content": text})
        async with self.redis.client() as client:
            await client.rpush(key, entry)
            await client.ltrim(key, -self.CONV_MAX, -1)

    async def get_conversation(self, user_id: int, limit: int) -> list[dict]:
        """Return the last ``limit`` messages of the conversation in chronological order."""
        key = f"{self.CONV}:{user_id}"
        async with self.redis.client() as client:
            raw = await client.lrange(key, -limit, -1)
        result = []
        for item in raw:
            try:
                result.append(json.loads(item))
            except (ValueError, TypeError):
                continue
        return result
