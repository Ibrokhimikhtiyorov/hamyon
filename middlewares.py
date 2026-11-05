import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

import services

logger = logging.getLogger(__name__)


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from_user = getattr(event, "from_user", None)
        if from_user is None:
            return await handler(event, data)

        try:
            db_user = await services.ensure_user(
                from_user.id, from_user.full_name, from_user.username
            )
        except Exception:
            logger.exception("Foydalanuvchini bazaga yozishda xatolik")
            return await handler(event, data)

        if db_user is not None and db_user["is_blocked"]:
            msg = "🚫 Hisobingiz bloklangan. Admin bilan bog'laning."
            if isinstance(event, CallbackQuery):
                await event.answer(msg, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(msg)
            return None

        data["db_user"] = db_user
        return await handler(event, data)


class ThrottlingMiddleware(BaseMiddleware):
    """Oddiy anti-flood: bir foydalanuvchidan juda tez so'rovlarni to'sadi."""

    def __init__(self, rate_seconds: float = 0.4):
        self.rate_seconds = rate_seconds
        self._last_seen: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from_user = getattr(event, "from_user", None)
        if from_user is not None:
            now = time.monotonic()
            last = self._last_seen.get(from_user.id, 0.0)
            if now - last < self.rate_seconds:
                if isinstance(event, CallbackQuery):
                    await event.answer("⏳ Iltimos, biroz sekinroq.", show_alert=False)
                return None
            self._last_seen[from_user.id] = now
            if len(self._last_seen) > 5000:
                cutoff = now - 300
                self._last_seen = {
                    uid: ts for uid, ts in self._last_seen.items() if ts >= cutoff
                }

        return await handler(event, data)
