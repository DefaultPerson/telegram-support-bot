from aiogram import Dispatcher
from aiogram_broadcast import BroadcastMiddleware
from aiogram_broadcast.ui import BroadcastUIMiddleware

from .album import AlbumMiddleware
from .manager import ManagerMiddleware
from .redis import RedisMiddleware
from .throttling import ThrottlingMiddleware


def register_middlewares(dp: Dispatcher, **kwargs) -> None:
    """
    Register bot middlewares.

    Args:
        dp (Dispatcher): The Aiogram Dispatcher instance.
        **kwargs: Expects ``pool`` (asyncpg pool), ``broadcast_storage``,
            ``broadcast_service`` and ``broadcast_scheduler``.

    Returns:
        None
    """
    # Register RedisMiddleware (user-layer storage over the PostgreSQL pool)
    dp.update.outer_middleware.register(RedisMiddleware(kwargs["pool"]))
    # Register ManagerMiddleware
    dp.update.outer_middleware.register(ManagerMiddleware())

    # Auto-register broadcast subscribers (private chats only)
    dp.update.outer_middleware.register(BroadcastMiddleware(kwargs["broadcast_storage"]))

    # Register AlbumMiddleware for message processing
    dp.message.middleware.register(AlbumMiddleware())
    # Register ThrottlingMiddleware for message processing
    dp.message.middleware.register(ThrottlingMiddleware())

    # Inject the broadcast UI manager (broadcast_ui) into handlers
    dp.update.middleware.register(
        BroadcastUIMiddleware(kwargs["broadcast_service"], kwargs["broadcast_scheduler"])
    )


__all__ = [
    "register_middlewares",
]
