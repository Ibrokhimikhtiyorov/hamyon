import asyncio
import logging

from aiogram import Bot

import services
from keyboards import order_actions

logger = logging.getLogger(__name__)


async def penalty_loop(bot: Bot, interval_seconds: int = 30) -> None:
    while True:
        try:
            events = await services.accrue_penalties()
            for tg_id, text, order_id in events:
                try:
                    await bot.send_message(
                        tg_id,
                        text,
                        reply_markup=order_actions(order_id) if order_id else None,
                    )
                except Exception as exc:
                    logger.warning("Jarima xabari yuborilmadi (%s): %s", tg_id, exc)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Jarima siklida xatolik")

        await asyncio.sleep(interval_seconds)
