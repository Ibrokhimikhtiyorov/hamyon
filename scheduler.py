import asyncio
import logging

from aiogram import Bot

import services
from config import PAYMENT_MINUTES
from keyboards import order_actions
from utils import fmt_sum

logger = logging.getLogger(__name__)


async def maintenance_loop(bot: Bot, interval_seconds: int = 30) -> None:
    """Har 30 soniyada: eslatmalar, jarimalar, avto-yakunlash va to'lov muddati."""
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

            expired = await services.expire_stale_hamyon_payments()
            for tg_id, amount in expired:
                try:
                    await bot.send_message(
                        tg_id,
                        "⏳ <b>To'lov vaqti tugadi</b>\n\n"
                        f"Berilgan {PAYMENT_MINUTES} daqiqa ichida to'lov "
                        "yakunlanmadi.\n"
                        f"💰 Summa: <b>{fmt_sum(amount)}</b>\n\n"
                        "To'lov bekor qilindi. Agar pulni allaqachon o'tkazgan "
                        "bo'lsangiz va u keyinroq tushsa, mablag' baribir "
                        "balansingizga qo'shiladi.\n"
                        "Yangi to'lov yaratish uchun qaytadan urinib ko'ring.",
                    )
                except Exception as exc:
                    logger.warning(
                        "To'lov muddati xabari yuborilmadi (%s): %s", tg_id, exc
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Maintenance siklida xatolik")

        await asyncio.sleep(interval_seconds)
