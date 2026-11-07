import asyncio
import logging
import sys
import os

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
base = os.path.join(BASE_DIR, "data")
os.makedirs(base, exist_ok=True)

from config import ADMIN_IDS, BOT_TOKEN, FSM_DB_PATH
from db import db
from handlers import admin, buyer, seller, start, wallet
from handlers.common import error_handler
from middlewares import ThrottlingMiddleware, UserMiddleware
from scheduler import penalty_loop
from sqlite_storage import SQLiteStorage


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def health(request):
    return web.Response(text="Telegram bot is running!")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info("HTTP server ishga tushdi. Port: %s", port)
    return runner


async def main() -> None:
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN topilmadi. Render Environment bo'limini tekshiring.")
        return

    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS bo'sh. Admin panel ishlamasligi mumkin.")

    bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    web_runner = await start_web_server()
    storage = None
    scheduler_task = None

    try:
        await db.connect()
        logger.info("Ma'lumotlar bazasi ulandi: %s", db.path)

        storage = SQLiteStorage(FSM_DB_PATH)
        await storage.connect()

        dispatcher = Dispatcher(storage=storage)

        dispatcher.message.middleware(ThrottlingMiddleware())
        dispatcher.callback_query.middleware(ThrottlingMiddleware())
        dispatcher.message.middleware(UserMiddleware())
        dispatcher.callback_query.middleware(UserMiddleware())

        dispatcher.include_routers(
            start.router,
            seller.router,
            buyer.router,
            wallet.router,
            admin.router,
        )

        dispatcher.errors.register(error_handler)

        try:
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Asosiy menyu"),
                    BotCommand(command="cancel", description="Amalni bekor qilish"),
                    BotCommand(command="help", description="Yordam"),
                ]
            )
        except Exception as exc:
            logger.warning("Buyruqlarni o'rnatib bo'lmadi: %s", exc)

        scheduler_task = asyncio.create_task(penalty_loop(bot))

        await bot.delete_webhook(drop_pending_updates=True)

        logger.info("Bot ishga tushdi")
        await dispatcher.start_polling(bot)

    finally:
        if scheduler_task:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass

        if storage:
            await storage.close()

        await db.close()
        await bot.session.close()
        await web_runner.cleanup()

        logger.info("Bot to'xtatildi")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass