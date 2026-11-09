"""HamyonAPI integration: payment creation and signed callbacks."""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from aiohttp import ClientSession, ClientTimeout, web

from config import (
    HAMYON_API_BASE,
    HAMYON_SHOP_ID,
    HAMYON_SHOP_KEY,
    HAMYON_TIMEOUT,
)
import services
from utils import esc, fmt_sum

logger = logging.getLogger(__name__)


def _signature_is_valid(form: Any) -> bool:
    if not HAMYON_SHOP_ID or not HAMYON_SHOP_KEY:
        logger.error("HamyonAPI SHOP_ID yoki SHOP_KEY sozlanmagan")
        return False

    payment_id = str(form.get("payment_id", ""))
    amount = str(form.get("amount", ""))
    received = str(form.get("sign", "")).lower()
    if not payment_id or not amount or not received:
        return False

    raw = f"{HAMYON_SHOP_ID}{payment_id}{amount}{HAMYON_SHOP_KEY}"
    expected = hashlib.md5(raw.encode("utf-8")).hexdigest()
    return hmac.compare_digest(expected, received)


async def create_payment(order_id: str, amount: int) -> dict[str, Any]:
    """Create payment and persist the returned HamyonAPI identifiers."""
    if not HAMYON_SHOP_ID or not HAMYON_SHOP_KEY:
        raise RuntimeError("HamyonAPI sozlamalari Render Environment'da topilmadi")

    timeout = ClientTimeout(total=HAMYON_TIMEOUT)
    payload = {
        "shop_id": HAMYON_SHOP_ID,
        "shop_key": HAMYON_SHOP_KEY,
        "amount": str(amount),
        "order_id": order_id,
    }

    async with ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{HAMYON_API_BASE}/payment/create", data=payload
        ) as response:
            try:
                result = await response.json(content_type=None)
            except Exception as exc:
                body = (await response.text())[:500]
                raise RuntimeError(
                    f"HamyonAPI JSON javob qaytarmadi (HTTP {response.status}): {body}"
                ) from exc

            if response.status >= 400:
                raise RuntimeError(
                    f"HamyonAPI HTTP {response.status}: {result}"
                )

    if not isinstance(result, dict):
        raise RuntimeError("HamyonAPI javobi noto'g'ri formatda")
    if result.get("error"):
        raise RuntimeError(str(result["error"]))

    payment_id = result.get("payment_id")
    if payment_id is None or str(payment_id).strip() == "":
        raise RuntimeError("HamyonAPI javobida payment_id topilmadi")

    card_value = result.get("card")
    card = str(card_value) if card_value is not None else None
    expires_raw = result.get("expires_in")
    try:
        expires_in = int(expires_raw) if expires_raw is not None else None
    except (TypeError, ValueError):
        expires_in = None

    await services.update_hamyon_payment_created(
        order_id, str(payment_id), card, expires_in
    )
    return result


async def hamyon_prepare(request: web.Request) -> web.Response:
    form = await request.post()
    if not _signature_is_valid(form):
        return web.json_response({"error": "imzo"}, status=403)

    order_id = str(form.get("order_id", "")).strip()
    payment_id = str(form.get("payment_id", "")).strip()
    try:
        amount = int(str(form.get("amount", "")))
    except (TypeError, ValueError):
        return web.json_response({"error": "amount"}, status=400)

    if not order_id or not payment_id or amount <= 0:
        return web.json_response({"error": "fields"}, status=400)

    try:
        accepted = await services.prepare_hamyon_payment(
            order_id, payment_id, amount
        )
    except Exception:
        logger.exception("HamyonAPI prepare callback bazaga yozilmadi")
        return web.json_response({"error": "database"}, status=500)

    if not accepted:
        return web.json_response({"error": "order_not_found_or_amount_mismatch"}, status=404)
    return web.json_response({"result": "ok"})


async def hamyon_complete(request: web.Request) -> web.Response:
    form = await request.post()
    if not _signature_is_valid(form):
        return web.json_response({"error": "imzo"}, status=403)

    payment_id = str(form.get("payment_id", "")).strip()
    status = str(form.get("status", "")).strip().lower()
    try:
        amount = int(str(form.get("amount", "")))
    except (TypeError, ValueError):
        return web.json_response({"error": "amount"}, status=400)

    if not payment_id or amount <= 0 or not status:
        return web.json_response({"error": "fields"}, status=400)

    if status != "paid":
        try:
            await services.cancel_hamyon_payment(payment_id)
        except Exception:
            logger.exception("HamyonAPI cancel callback bazaga yozilmadi")
            return web.json_response({"error": "database"}, status=500)
        return web.json_response({"result": "ok"})

    try:
        payment = await services.complete_hamyon_payment(payment_id, amount)
    except Exception:
        logger.exception("HamyonAPI paid callback bajarilmadi")
        return web.json_response({"error": "database"}, status=500)

    if payment is None:
        return web.json_response({"error": "payment_not_found_or_amount_mismatch"}, status=404)

    if payment["credited"]:
        bot = request.app.get("bot")
        if bot is not None:
            try:
                await bot.send_message(
                    payment["user_tg"],
                    "✅ <b>To'lov muvaffaqiyatli tasdiqlandi!</b>\n\n"
                    f"💰 To'ldirilgan summa: <b>{fmt_sum(payment['amount'])}</b>\n"
                    f"👛 Yangi balans: <b>{fmt_sum(payment['balance'])}</b>",
                )
            except Exception:
                # Telegram xabari yuborilmasa ham to'lov tasdiqlangan bo'ladi.
                logger.exception("To'lov xabari foydalanuvchiga yuborilmadi")

    return web.json_response({"result": "ok"})
