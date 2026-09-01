from __future__ import annotations

from asyncio import sleep
from typing import Any, Awaitable, Callable, Dict, MutableMapping, Optional, Tuple

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from cachetools import TTLCache

from ..types.album import Album, Media


class AlbumMiddleware(BaseMiddleware):
    """
    Middleware for accepting media groups (Album message).
    """

    def __init__(
            self,
            album_key: str = "album",
            latency: float = 0.5,
            ttl: float = 5.0,
    ) -> None:
        """
        Initialize the AlbumMiddleware.

        :param album_key: The key to store the album data in the data dictionary.
        :param latency: The latency in seconds to wait before processing the album.
        :param ttl: The time-to-live in seconds for the cache entries. It must stay
            comfortably above ``latency``: if an entry expires while the album is
            still arriving, a straggling message starts a second cache entry and
            the album is forwarded twice.
        """
        if ttl <= latency:
            raise ValueError("ttl must be greater than latency")
        self.album_key = album_key
        self.latency = latency
        self.cache: MutableMapping[str, Dict[str, Any]] = TTLCache(maxsize=10_000, ttl=ttl)

    @staticmethod
    def get_content(message: Message) -> Optional[Tuple[Media, str]]:
        """
        Get the content type and media from a message.

        :param message: The message object.
        :return: A tuple containing the media and content type, or None if no valid media found.
        """
        if message.photo:
            return message.photo[-1], "photo"
        if message.video:
            return message.video, "video"
        if message.audio:
            return message.audio, "audio"
        if message.document:
            return message.document, "document"
        return None

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any],
    ) -> Any:
        """
        Call the middleware.

        :param handler: The handler function.
        :param event: The Telegram event.
        :param data: Additional data.
        :return: The result of the handler function.
        """
        # Check if the event is a message with a media group ID
        if isinstance(event, Message) and event.media_group_id is not None:
            content = self.get_content(event)

            # Media groups may carry types this middleware does not aggregate
            # (paid media, for example): let them through as plain messages.
            if content is None:
                return await handler(event, data)

            media, content_type = content
            key = event.media_group_id
            entry = self.cache.get(key)

            # If the media group ID is already in the cache, only collect the media:
            # the first message of the group is the one that runs the handler.
            if entry is not None:
                entry["messages"].append(event)
                entry.setdefault(content_type, []).append(media)
                return None

            # If the media group ID is not in the cache, add it with the media data
            entry = {
                content_type: [media],
                "messages": [event],
                "caption": event.html_text,
            }
            self.cache[key] = entry
            await sleep(self.latency)

            # Validate the album data using the Album model. `entry` is kept as a
            # local reference so an expired cache entry cannot lose the album.
            data[self.album_key] = Album.model_validate(entry, context={"bot": data["bot"]})

        # Call the handler function with the event and data
        return await handler(event, data)
