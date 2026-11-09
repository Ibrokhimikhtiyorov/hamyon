import logging

from aiogram.types import ErrorEvent

logger = logging.getLogger(__name__)


async def error_handler(event: ErrorEvent) -> bool:
    logger.exception("Update ni qayta ishlashda xatolik", exc_info=event.exception)
    update = event.update
    text = (
        "❌ Kutilmagan xatolik yuz berdi. Iltimos, birozdan so'ng qaytadan "
        "urinib ko'ring yoki admin bilan bog'laning."
    )
    try:
        if update.callback_query is not None:
            await update.callback_query.answer("Xatolik yuz berdi", show_alert=True)
        elif update.message is not None:
            await update.message.answer(text)
    except Exception:
        pass
    return True
