import asyncio
import logging

import asyncpg
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram_broadcast import BroadcastScheduler, BroadcastService, PostgresBroadcastStorage
from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .bot import commands
from .bot.handlers import include_routers
from .bot.llm import get_provider
from .bot.middlewares import register_middlewares
from .bot.policy import load_policy
from .bot.utils.redis import create_schema
from .config import Config, load_config
from .logger import setup_logger


async def on_shutdown(
    apscheduler: AsyncIOScheduler,
    dispatcher: Dispatcher,
    config: Config,
    bot: Bot,
    pg_pool: asyncpg.Pool,
) -> None:
    """
    Shutdown event handler. This runs when the bot shuts down.

    :param apscheduler: AsyncIOScheduler: The apscheduler instance.
    :param dispatcher: Dispatcher: The bot dispatcher.
    :param config: Config: The config instance.
    :param bot: Bot: The bot instance.
    :param pg_pool: asyncpg.Pool: The PostgreSQL connection pool.
    """
    # Stop apscheduler
    apscheduler.shutdown()
    # Delete commands and close storages when shutting down
    await commands.delete(bot, config)
    await dispatcher.storage.close()
    await pg_pool.close()
    await bot.delete_webhook()
    await bot.session.close()


async def on_startup(
    apscheduler: AsyncIOScheduler,
    config: Config,
    bot: Bot,
) -> None:
    """
    Startup event handler. This runs when the bot starts up.

    :param apscheduler: AsyncIOScheduler: The apscheduler instance.
    :param config: Config: The config instance.
    :param bot: Bot: The bot instance.
    """
    # Start apscheduler
    apscheduler.start()
    # Setup commands when starting up
    await commands.setup(bot, config)


async def main() -> None:
    """
    Main function that initializes the bot and starts the event loop.
    """
    # Load config
    config = load_config()

    # Initialize apscheduler (Redis job store; password optional)
    job_store = RedisJobStore(
        host=config.redis.HOST,
        port=config.redis.PORT,
        db=config.redis.DB,
        password=config.redis.PASSWORD or None,
    )
    apscheduler = AsyncIOScheduler(
        jobstores={"default": job_store},
    )

    # Initialize Redis FSM storage (password carried in the DSN)
    storage = RedisStorage.from_url(
        url=config.redis.dsn(),
    )

    # Initialize the PostgreSQL pool and create schemas (user layer + subscribers)
    pg_pool = await asyncpg.create_pool(config.db.URL)
    await create_schema(pg_pool)
    broadcast_storage = PostgresBroadcastStorage(pg_pool)
    await broadcast_storage.create_schema()

    # Create Bot and Dispatcher instances
    bot = Bot(
        token=config.bot.TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    # Broadcast service + scheduler (aiogram-broadcast)
    broadcast_service = BroadcastService(bot, broadcast_storage)
    broadcast_scheduler = BroadcastScheduler(broadcast_service, apscheduler)

    dp = Dispatcher(
        apscheduler=apscheduler,
        storage=storage,
        config=config,
        bot=bot,
    )
    # Expose for handlers / shutdown
    dp["broadcast_storage"] = broadcast_storage
    dp["pg_pool"] = pg_pool

    # Optional policy engine and LLM provider (both disabled by default).
    # Exposed as workflow data so aiogram injects them into handlers as kwargs.
    # A bad/missing policy config must never crash the bot — log and continue.
    policy_engine = None
    if config.policy.ENABLED:
        try:
            policy_engine = load_policy(config.policy)
        except Exception as ex:  # noqa: BLE001
            logging.error("Failed to load policy; continuing without it: %s", ex)
    dp["policy_engine"] = policy_engine
    dp["llm_provider"] = get_provider(config.ai)

    # Register startup handler
    dp.startup.register(on_startup)
    # Register shutdown handler
    dp.shutdown.register(on_shutdown)

    # Include routes
    include_routers(dp)
    # Register middlewares
    register_middlewares(
        dp,
        pool=pg_pool,
        broadcast_storage=broadcast_storage,
        broadcast_service=broadcast_service,
        broadcast_scheduler=broadcast_scheduler,
    )

    # Start the bot. Keep pending updates so messages sent while the bot was
    # offline (e.g. during a redeploy) are processed, not dropped.
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    # Set up logging
    setup_logger()
    # Run the bot
    asyncio.run(main())
