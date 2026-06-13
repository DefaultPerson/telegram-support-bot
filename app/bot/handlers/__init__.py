from aiogram import Dispatcher
from aiogram_broadcast.ui import BroadcastUIHandlers

from . import errors, group, private


def include_routers(dp: Dispatcher) -> None:
    """
    Include bot routers.

    :param dp: Dispatcher object.
    :return: None
    """
    dp.include_routers(
        *[
            *group.routers,
            *private.routers,
            errors.router,
        ]
    )
    BroadcastUIHandlers().register(dp)


__all__ = [
    "include_routers",
]
