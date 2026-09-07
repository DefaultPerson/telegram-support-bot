from __future__ import annotations

import base64
import logging
from typing import List, Optional, Sequence, Tuple

from aiogram import Bot
from aiogram.types import Document, Message, PhotoSize

from app.bot.types.album import Album

logger = logging.getLogger(__name__)

# Telegram always re-encodes `photo` uploads as JPEG; documents carry their own type.
_PHOTO_MIME = "image/jpeg"

Attachment = Tuple[str, str]  # (file_id, mime_type)


def _document_attachment(document: Document) -> Optional[Attachment]:
    """Return the attachment for an image sent as a file, or None for other types."""
    mime_type = document.mime_type or ""
    if not mime_type.startswith("image/"):
        return None
    return document.file_id, mime_type


def _largest(photo: Sequence[PhotoSize]) -> PhotoSize:
    """Telegram sends every rendition of a photo; the last one is the largest."""
    return photo[-1]


def collect_attachments(message: Message, album: Optional[Album]) -> List[Attachment]:
    """
    List the images attached to an incoming message, newest scenario first.

    Albums arrive as several messages, so the aggregated :class:`Album` is the
    only place holding all of them; a lone message carries at most one image.
    """
    attachments: List[Attachment] = []

    if album is not None:
        for photo in album.photo or []:
            attachments.append((photo.file_id, _PHOTO_MIME))
        for document in album.document or []:
            attachment = _document_attachment(document)
            if attachment is not None:
                attachments.append(attachment)
        return attachments

    if message.photo:
        attachments.append((_largest(message.photo).file_id, _PHOTO_MIME))
    elif message.document:
        attachment = _document_attachment(message.document)
        if attachment is not None:
            attachments.append(attachment)

    return attachments


async def as_data_urls(
    bot: Bot,
    attachments: Sequence[Attachment],
    max_images: int,
    max_bytes: int,
) -> List[str]:
    """
    Download attachments and encode them as base64 data URLs for the LLM.

    Best-effort: an image that cannot be downloaded is skipped rather than
    failing the whole draft, since the text alone still produces a useful reply.

    :param bot: The bot used to download the files.
    :param attachments: The attachments returned by :func:`collect_attachments`.
    :param max_images: Hard cap on how many images are sent to the model.
    :param max_bytes: Per-image size ceiling; larger images are skipped.
    :return: A list of ``data:<mime>;base64,<payload>`` strings.
    """
    if max_images <= 0:
        return []

    data_urls: List[str] = []
    for file_id, mime_type in attachments[:max_images]:
        try:
            buffer = await bot.download(file_id)
            if buffer is None:
                continue
            payload = buffer.read()
        except Exception as ex:  # noqa: BLE001 - never block the draft on a download
            logger.warning("Skipping image %s: %s: %s", file_id, type(ex).__name__, ex)
            continue

        if len(payload) > max_bytes:
            logger.info(
                "Skipping image %s: %d bytes exceeds AI_IMAGE_MAX_BYTES=%d.",
                file_id,
                len(payload),
                max_bytes,
            )
            continue

        encoded = base64.b64encode(payload).decode("ascii")
        data_urls.append(f"data:{mime_type};base64,{encoded}")

    if len(attachments) > max_images:
        logger.info(
            "Attached %d of %d images (AI_MAX_IMAGES).", len(data_urls), len(attachments)
        )

    return data_urls
