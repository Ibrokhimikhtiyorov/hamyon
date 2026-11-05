import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

from config import ADMIN_IDS
from utils import code, esc, fmt_dt, fmt_sum

logger = logging.getLogger(__name__)


async def notify_admins(bot: Bot, text: str, reply_markup: InlineKeyboardMarkup | None = None):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup)
        except Exception as exc:
            logger.warning("Adminga xabar yuborilmadi (%s): %s", admin_id, exc)


async def send_topup_card(
    bot: Bot, chat_id: int, topup, reply_markup: InlineKeyboardMarkup | None = None
):
    text = (
        "💳 <b>Balans to'ldirish so'rovi</b>\n\n"
        f"👤 Foydalanuvchi: {esc(topup['user_name'])}\n"
        f"🆔 Telegram ID: {code(topup['user_tg'])}\n"
        f"🔗 Username: @{esc(topup['username'] or 'yo`q')}\n"
        f"💰 Summa: <b>{fmt_sum(topup['amount'])}</b>\n"
        f"🧾 To'lov turi: {esc(topup['receipt_type'])}\n"
        f"🕐 Vaqt: {fmt_dt(topup['created_at'])}\n\n"
        f"So'rov ID: {code(topup['id'])}"
    )
    try:
        if topup["receipt_type"] == "photo":
            await bot.send_photo(
                chat_id,
                topup["receipt_value"],
                caption=text,
                reply_markup=reply_markup,
            )
        elif topup["receipt_type"] == "document":
            await bot.send_document(
                chat_id,
                topup["receipt_value"],
                caption=text,
                reply_markup=reply_markup,
            )
        else:
            await bot.send_message(
                chat_id,
                f"{text}\n\n📝 Chek: {code(topup['receipt_value'])}",
                reply_markup=reply_markup,
            )
    except Exception as exc:
        logger.warning("Topup kartasi yuborilmadi (%s): %s", chat_id, exc)
        try:
            await bot.send_message(
                chat_id,
                f"{text}\n\n📝 Chek (yuborib bo'lmadi): {code(topup['receipt_value'])}",
                reply_markup=reply_markup,
            )
        except Exception:
            pass


async def notify_admin_topup(bot: Bot, topup, reply_markup: InlineKeyboardMarkup | None = None):
    for admin_id in ADMIN_IDS:
        await send_topup_card(bot, admin_id, topup, reply_markup)


def withdrawal_text(withdrawal) -> str:
    return (
        "💸 <b>Pul yechish so'rovi</b>\n\n"
        f"👤 Sotuvchi: {esc(withdrawal['user_name'])}\n"
        f"🆔 Telegram ID: {code(withdrawal['user_tg'])}\n"
        f"🔑 Sotuvchi kodi: {code(withdrawal['seller_code'] or '-')}\n"
        f"💰 Summa: <b>{fmt_sum(withdrawal['amount'])}</b>\n"
        f"💳 Karta: {code(withdrawal['card_number'])}\n"
        f"🕐 Vaqt: {fmt_dt(withdrawal['created_at'])}\n\n"
        f"So'rov ID: {code(withdrawal['id'])}"
    )


async def notify_admin_withdrawal(bot: Bot, withdrawal, reply_markup: InlineKeyboardMarkup | None = None):
    await notify_admins(bot, withdrawal_text(withdrawal), reply_markup)


def dispute_text(dispute) -> str:
    return (
        "⚠️ <b>Shikoyat</b>\n\n"
        f"Buyurtma: <b>#{dispute['order_id']}</b>\n"
        f"Akkaunt: #{dispute['account_id']}\n"
        f"Xaridor: {esc(dispute['buyer_name'])} ({code(dispute['buyer_tg'])})\n"
        f"Sotuvchi: {esc(dispute['seller_name'])} ({code(dispute['seller_tg'])})\n"
        f"Narx: <b>{fmt_sum(dispute['price'])}</b>\n"
        f"Jarima: <b>{fmt_sum(dispute['penalty'])}</b>\n"
        f"Sabab: {esc(dispute['reason'])}\n"
        f"Vaqt: {fmt_dt(dispute['created_at'])}"
    )


async def notify_admin_dispute(bot: Bot, dispute, reply_markup: InlineKeyboardMarkup | None = None):
    await notify_admins(bot, dispute_text(dispute), reply_markup)
