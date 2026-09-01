import asyncio

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Chat, Document, Message, PhotoSize

from app.bot.middlewares.album import AlbumMiddleware
from app.bot.types.album import Album

DATE = 1788205966


@pytest.fixture()
def bot() -> Bot:
    return Bot(
        token="123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        default=DefaultBotProperties(parse_mode="HTML"),
    )


def make_document(file_id: str) -> Document:
    return Document(
        file_id=file_id,
        file_unique_id=f"u{file_id}",
        file_name="IMG_4316.PNG",
        mime_type="image/png",
        file_size=1983564,
    )


def make_message(bot: Bot, message_id: int, document: Document) -> Message:
    message = Message(
        message_id=message_id,
        date=DATE,
        chat=Chat(id=1, type="private"),
        media_group_id="14305647731613316",
        document=document,
    )
    return message.as_(bot)


def build_album(bot: Bot, caption: str, documents: list[Document]) -> Album:
    return Album.model_validate(
        {"document": documents, "messages": [], "caption": caption},
        context={"bot": bot},
    )


def test_media_group_carries_caption_on_first_item_only(bot: Bot) -> None:
    album = build_album(bot, "<b>hi</b>", [make_document("F1"), make_document("F2")])

    group = album.as_media_group

    assert [m.media for m in group] == ["F1", "F2"]
    assert [m.caption for m in group] == ["<b>hi</b>", None]
    assert all(m.parse_mode == "HTML" for m in group)


def test_empty_caption_is_not_sent(bot: Bot) -> None:
    # aiogram Message.html_text returns "" for a caption-less document.
    album = build_album(bot, "", [make_document("F1")])

    assert album.as_media_group[0].caption is None


def test_copy_to_builds_send_media_group(bot: Bot) -> None:
    album = build_album(bot, "", [make_document("F1"), make_document("F2")])

    call = album.copy_to(chat_id=-100123, message_thread_id=7)

    assert len(call.media) == 2
    assert call.message_thread_id == 7


def test_mixed_media_types_keep_a_single_caption(bot: Bot) -> None:
    album = Album.model_validate(
        {
            "photo": [PhotoSize(file_id="P1", file_unique_id="uP1", width=1, height=1)],
            "document": [make_document("F1")],
            "messages": [],
            "caption": "cap",
        },
        context={"bot": bot},
    )

    assert [m.caption for m in album.as_media_group] == ["cap", None]


def test_ttl_must_exceed_latency() -> None:
    with pytest.raises(ValueError):
        AlbumMiddleware(latency=0.5, ttl=0.3)


def test_middleware_collects_every_message_of_the_group(bot: Bot) -> None:
    middleware = AlbumMiddleware(latency=0.01, ttl=1.0)
    seen: list[Album] = []

    async def handler(event, data):
        seen.append(data["album"])

    messages = [make_message(bot, i, make_document(f"F{i}")) for i in range(1, 4)]

    async def run() -> None:
        # The stragglers must reach the cache before the first message wakes up.
        first = asyncio.create_task(middleware(handler, messages[0], {"bot": bot}))
        await asyncio.sleep(0)
        for message in messages[1:]:
            await middleware(handler, message, {"bot": bot})
        await first

    asyncio.run(run())

    assert len(seen) == 1
    album = seen[0]
    assert [m.media for m in album.as_media_group] == ["F1", "F2", "F3"]
    assert len(album.messages) == 3


def test_middleware_collects_messages_across_media_types(bot: Bot) -> None:
    """A second content type used to overwrite the bucket and drop its message."""
    middleware = AlbumMiddleware(latency=0.01, ttl=1.0)
    seen: list[Album] = []

    async def handler(event, data):
        seen.append(data["album"])

    photo_message = Message(
        message_id=1,
        date=DATE,
        chat=Chat(id=1, type="private"),
        media_group_id="14305647731613316",
        photo=[PhotoSize(file_id="P1", file_unique_id="uP1", width=1, height=1)],
    ).as_(bot)
    document_message = make_message(bot, 2, make_document("F1"))

    async def run() -> None:
        first = asyncio.create_task(middleware(handler, photo_message, {"bot": bot}))
        await asyncio.sleep(0)
        await middleware(handler, document_message, {"bot": bot})
        await first

    asyncio.run(run())

    album = seen[0]
    assert [m.media for m in album.as_media_group] == ["P1", "F1"]
    assert len(album.messages) == 2


def test_middleware_ignores_media_groups_without_known_content(bot: Bot) -> None:
    middleware = AlbumMiddleware(latency=0.01, ttl=1.0)
    calls: list[dict] = []

    async def handler(event, data):
        calls.append(data)
        return "handled"

    message = Message(
        message_id=1,
        date=DATE,
        chat=Chat(id=1, type="private"),
        media_group_id="1",
    ).as_(bot)

    result = asyncio.run(middleware(handler, message, {"bot": bot}))

    assert result == "handled"
    assert "album" not in calls[0]
