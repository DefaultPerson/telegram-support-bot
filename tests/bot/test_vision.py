import asyncio
import base64
import logging

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Chat, Document, Message, PhotoSize

from app.bot.types.album import Album
from app.bot.utils.vision import as_data_urls, collect_attachments

DATE = 1788205966
PNG = b"\x89PNG\r\n\x1a\n" + b"payload"


@pytest.fixture()
def bot() -> Bot:
    return Bot(
        token="123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        default=DefaultBotProperties(parse_mode="HTML"),
    )


class _FakeBot:
    """Stands in for aiogram's Bot.download, which hits the network."""

    def __init__(self, payloads=None, fail=False) -> None:
        self.payloads = payloads or {}
        self.fail = fail
        self.downloaded: list[str] = []

    async def download(self, file_id):
        self.downloaded.append(file_id)
        if self.fail:
            raise RuntimeError("file is too big")

        class _Buffer:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

        return _Buffer(self.payloads.get(file_id, PNG))


def photo(file_id: str, size: int = 100) -> PhotoSize:
    return PhotoSize(file_id=file_id, file_unique_id=f"u{file_id}", width=size, height=size)


def image_document(file_id: str, mime_type: str = "image/png") -> Document:
    return Document(
        file_id=file_id,
        file_unique_id=f"u{file_id}",
        file_name="IMG_4316.PNG",
        mime_type=mime_type,
    )


def make_message(bot: Bot, **kwargs) -> Message:
    return Message(
        message_id=1, date=DATE, chat=Chat(id=1, type="private"), **kwargs
    ).as_(bot)


def test_photo_uses_the_largest_rendition(bot: Bot) -> None:
    message = make_message(bot, photo=[photo("small", 90), photo("large", 1280)])

    assert collect_attachments(message, None) == [("large", "image/jpeg")]


def test_screenshot_sent_as_a_document_is_collected(bot: Bot) -> None:
    """The crash in #51 was exactly this: a PNG delivered as a document."""
    message = make_message(bot, document=image_document("F1"))

    assert collect_attachments(message, None) == [("F1", "image/png")]


def test_non_image_documents_are_ignored(bot: Bot) -> None:
    message = make_message(bot, document=image_document("F1", mime_type="application/pdf"))

    assert collect_attachments(message, None) == []


def test_message_without_attachments(bot: Bot) -> None:
    assert collect_attachments(make_message(bot, text="hi"), None) == []


def test_album_contributes_every_image(bot: Bot) -> None:
    album = Album.model_validate(
        {
            "photo": [photo("P1"), photo("P2")],
            "document": [image_document("F1")],
            "messages": [],
            "caption": "cap",
        },
        context={"bot": bot},
    )

    assert collect_attachments(make_message(bot, text="x"), album) == [
        ("P1", "image/jpeg"),
        ("P2", "image/jpeg"),
        ("F1", "image/png"),
    ]


def test_data_urls_are_base64_encoded() -> None:
    fake = _FakeBot()

    urls = asyncio.run(as_data_urls(fake, [("P1", "image/jpeg")], max_images=4, max_bytes=10_000))

    assert urls == [f"data:image/jpeg;base64,{base64.b64encode(PNG).decode()}"]


def test_images_beyond_the_cap_are_dropped() -> None:
    fake = _FakeBot()
    attachments = [(f"P{i}", "image/jpeg") for i in range(6)]

    urls = asyncio.run(as_data_urls(fake, attachments, max_images=2, max_bytes=10_000))

    assert len(urls) == 2
    assert fake.downloaded == ["P0", "P1"]


def test_oversized_images_are_skipped(caplog) -> None:
    fake = _FakeBot()

    with caplog.at_level(logging.INFO):
        urls = asyncio.run(
            as_data_urls(fake, [("P1", "image/jpeg")], max_images=4, max_bytes=4)
        )

    assert urls == []
    assert "AI_IMAGE_MAX_BYTES" in caplog.text


def test_download_failure_does_not_break_the_draft(caplog) -> None:
    fake = _FakeBot(fail=True)

    with caplog.at_level(logging.WARNING):
        urls = asyncio.run(
            as_data_urls(fake, [("P1", "image/jpeg")], max_images=4, max_bytes=10_000)
        )

    assert urls == []
    assert "Skipping image" in caplog.text


def test_vision_disabled_downloads_nothing() -> None:
    fake = _FakeBot()

    assert asyncio.run(as_data_urls(fake, [("P1", "image/jpeg")], 0, 10_000)) == []
    assert fake.downloaded == []
