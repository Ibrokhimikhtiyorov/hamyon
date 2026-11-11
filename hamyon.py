"""HamyonAPI integration: payment creation and signed callbacks."""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

from aiohttp import ClientSession, ClientTimeout, web

from config import (
    CHECK_MINUTES,
    HAMYON_API_BASE,
    HAMYON_SHOP_ID,
    HAMYON_SHOP_KEY,
    HAMYON_TIMEOUT,
    PENALTY_INTERVAL_MINUTES,
    PENALTY_PERCENT,
    SELLER_SHARE_PERCENT,
)
import services
from keyboards import order_actions
from notifications import notify_admin_payment_received, notify_admin_purchase
from utils import code, fmt_dt, fmt_sum

logger = logging.getLogger(__name__)


class _WebhookRateLimiter:
    """Noto'g'ri imzo urinishlarini cheklaydi (imzo brute-force himoyasi).

    Faqat RAD etilgan (403) so'rovlar hisoblanadi — to'g'ri imzoli
    HamyonAPI callback'lari hech qachon bloklanmaydi.
    """

    def __init__(self, max_failures: int = 20, window_seconds: float = 60.0) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, list[float]] = {}

    @staticmethod
    def client_ip(request: web.Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.remote or "unknown"

    def is_blocked(self, ip: str) -> bool:
        now = time.monotonic()
        hits = [t for t in self._failures.get(ip, ()) if now - t < self.window]
        self._failures[ip] = hits
        return len(hits) >= self.max_failures

    def register_failure(self, ip: str) -> None:
        now = time.monotonic()
        self._failures.setdefault(ip, []).append(now)
        if len(self._failures) > 5000:
            cutoff = now - self.window
            self._failures = {
                key: hits
                for key, hits in (
                    (k, [t for t in v if t >= cutoff]) for k, v in self._failures.items()
                )
                if hits
            }


_rate_limiter = _WebhookRateLimiter()


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
    ip = _rate_limiter.client_ip(request)
    if _rate_limiter.is_blocked(ip):
        return web.json_response({"error": "too_many_requests"}, status=429)
    form = await request.post()
    if not _signature_is_valid(form):
        _rate_limiter.register_failure(ip)
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


async def _finish_purchase(bot, payment: dict, account_id: int) -> bool:
    """To'g'ridan-to'g'ri to'lovdan keyin xaridni avtomatik yakunlaydi.

    Akkaunt allaqachon sotilgan bo'lsa False qaytaradi — mablag' balansda qoladi.
    """
    buyer = await services.get_user_by_tg(payment["user_tg"])
    if buyer is None:
        return False
    try:
        order, account, seller = await services.create_order(buyer["id"], account_id)
        login, password = await services.get_credentials(order["id"], buyer["id"])
    except services.ServiceError:
        return False

    try:
        await bot.send_message(
            payment["user_tg"],
            "✅ <b>To'lov qabul qilindi — xarid muvaffaqiyatli!</b>\n\n"
            f"📦 Akkaunt: <b>#{account['id']}</b>\n"
            f"🔑 Login: {code(login)}\n"
            f"🔒 Parol: {code(password)}\n\n"
            f"💰 To'langan: <b>{fmt_sum(account['price'])}</b>\n"
            f"⏰ Tekshirish muddati: <b>{fmt_dt(order['check_deadline'])}</b> gacha "
            f"({CHECK_MINUTES} daqiqa)\n\n"
            f"⚠️ Muddat tugagach har bir {PENALTY_INTERVAL_MINUTES} daqiqa kechikish "
            f"uchun narxning {PENALTY_PERCENT}% jarima hisoblanadi.\n"
            "Akkauntni tekshirib, natijaga ko'ra tasdiqlang yoki shikoyat qiling.",
            reply_markup=order_actions(order["id"]),
            protect_content=True,
        )
    except Exception:
        logger.exception("Xarid xabari xaridorga yuborilmadi")

    try:
        await bot.send_message(
            seller["tg_id"],
            "🎉 <b>Akkauntingiz sotildi!</b>\n\n"
            f"📦 Akkaunt: <b>#{account['id']}</b>\n"
            f"💰 Narx: <b>{fmt_sum(account['price'])}</b>\n"
            f"🆔 Buyurtma: <b>#{order['id']}</b>\n\n"
            f"Xaridor akkauntni tekshirib tasdiqlagach, mablag'ning "
            f"{SELLER_SHARE_PERCENT}% balansingizga qo'shiladi.",
        )
    except Exception:
        pass

    await notify_admin_purchase(
        bot,
        buyer_name=payment["user_name"],
        buyer_tg=payment["user_tg"],
        account_id=account["id"],
        price=account["price"],
        order_id=order["id"],
        seller_name=seller["full_name"],
        seller_code=seller["seller_code"],
        method="HamyonAPI (to'g'ridan-to'g'ri)",
    )
    return True


async def hamyon_complete(request: web.Request) -> web.Response:
    ip = _rate_limiter.client_ip(request)
    if _rate_limiter.is_blocked(ip):
        return web.json_response({"error": "too_many_requests"}, status=429)
    form = await request.post()
    if not _signature_is_valid(form):
        _rate_limiter.register_failure(ip)
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

    if not payment["credited"]:
        return web.json_response({"result": "ok"})

    bot = request.app.get("bot")
    if bot is None:
        return web.json_response({"result": "ok"})

    purpose = str(payment.get("purpose") or "topup")
    purchase_account_id = 0
    if purpose.startswith("buy:"):
        try:
            purchase_account_id = int(purpose.split(":", 1)[1])
        except (TypeError, ValueError):
            purchase_account_id = 0

    purchased = False
    if purchase_account_id:
        purchased = await _finish_purchase(bot, payment, purchase_account_id)

    if purchased:
        return web.json_response({"result": "ok"})

    try:
        if purchase_account_id:
            await bot.send_message(
                payment["user_tg"],
                "✅ <b>To'lov qabul qilindi</b>\n\n"
                "ℹ️ Akkaunt buyurtma vaqtida allaqachon sotilib ulgurgan.\n"
                f"💰 Mablag' balansingizga o'tdi: <b>{fmt_sum(payment['amount'])}</b>\n"
                f"👛 Yangi balans: <b>{fmt_sum(payment['balance'])}</b>\n\n"
                "Sotuvchi kodini qayta terib, boshqa akkaunt tanlang.",
            )
        else:
            await bot.send_message(
                payment["user_tg"],
                "✅ <b>To'lov muvaffaqiyatli tasdiqlandi!</b>\n\n"
                f"💰 To'ldirilgan summa: <b>{fmt_sum(payment['amount'])}</b>\n"
                f"👛 Yangi balans: <b>{fmt_sum(payment['balance'])}</b>",
            )
    except Exception:
        # Telegram xabari yuborilmasa ham to'lov tasdiqlangan bo'ladi.
        logger.exception("To'lov xabari foydalanuvchiga yuborilmadi")

    note = (
        "Akkaunt xaridi uchun (balansga o'tdi)"
        if purchase_account_id
        else "Balans to'ldirish (HamyonAPI)"
    )
    try:
        await notify_admin_payment_received(
            bot,
            user_name=payment["user_name"],
            username=payment["username"],
            user_tg=payment["user_tg"],
            amount=payment["amount"],
            payment_id=payment["payment_id"],
            balance=payment["balance"],
            note=note,
        )
    except Exception:
        logger.exception("Admin SMS yuborilmadi")

    return web.json_response({"result": "ok"})
