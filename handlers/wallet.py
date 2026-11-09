import asyncio
import logging
import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import services
from config import COMMISSION_PERCENT, MIN_TOPUP, PAYMENT_CARD, PAYMENT_HOLDER
from keyboards import (
    back_to_menu, cancel_kb, topup_payment_method_kb, topup_review_kb, wallet_menu,
)
from notifications import notify_admin_topup
import hamyon
from states import TopUp
from utils import code, esc, fmt_dt, fmt_sum, parse_amount

router = Router(name="wallet")
logger = logging.getLogger(__name__)

MAX_TOPUP = 50_000_000

TYPE_LABELS = {
    "topup": "➕ Balans to'ldirish",
    "purchase": "🛒 Akkaunt xaridi",
    "penalty": "⏰ Kechikish jarimasi",
    "sale": "💰 Savdo daromadi",
    "commission": "🏦 Bot komissiyasi",
    "withdraw": "💸 Pul yechish",
    "withdraw_refund": "↩️ Pul yechish bekor qilindi",
    "refund": "↩️ Xarid uchun qaytarish",
    "adjustment": "⚙️ Admin tuzatishi",
}

REQUEST_STATUS_LABELS = {
    "pending": "⏳ Kutilmoqda",
    "approved": "✅ Tasdiqlangan",
    "paid": "✅ To'langan",
    "cancelled": "❌ Bekor qilingan",
    "failed": "⚠️ Xatolik",
    "rejected": "❌ Rad etilgan",
}


async def show_wallet(message: Message, db_user):
    user = await services.get_user(db_user["id"])
    await message.answer(
        "👛 <b>Hamyon</b>\n\n"
        f"💰 Balans: <b>{fmt_sum(user['balance'])}</b>\n\n"
        f"Platforma har bir savdodan {COMMISSION_PERCENT}% komissiya oladi.\n"
        "Balansni HamyonAPI orqali avtomatik yoki karta chekini yuborib "
        "admin tasdig'i bilan to'ldirishingiz mumkin.",
        reply_markup=wallet_menu(),
    )


@router.callback_query(F.data == "wallet:menu")
async def cb_wallet(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    await callback.answer()
    await show_wallet(callback.message, db_user)


@router.callback_query(F.data == "wallet:topup")
async def cb_topup(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    await callback.answer()
    await state.set_state(TopUp.amount)
    await callback.message.answer(
        "💳 <b>Balansni to'ldirish</b>\n\n"
        f"To'ldirmoqchi bo'lgan summani kiriting (so'mda, faqat raqam).\n\n"
        f"Minimal: {fmt_sum(MIN_TOPUP)}\n"
        f"Maksimal: {fmt_sum(MAX_TOPUP)}",
        reply_markup=cancel_kb(),
    )


@router.message(TopUp.amount, F.text)
async def topup_amount(message: Message, state: FSMContext):
    amount = parse_amount(message.text)
    if amount is None or not MIN_TOPUP <= amount <= MAX_TOPUP:
        await message.answer(
            f"❌ Summa noto'g'ri. {fmt_sum(MIN_TOPUP)} dan "
            f"{fmt_sum(MAX_TOPUP)} gacha kiriting."
        )
        return

    await state.update_data(amount=amount)
    await state.set_state(TopUp.amount)
    await message.answer(
        "💳 <b>To'lov usulini tanlang</b>\n\n"
        f"To'ldirish summasi: <b>{fmt_sum(amount)}</b>\n\n"
        "⚡ HamyonAPI — to'lov avtomatik tasdiqlanadi.\n"
        "💳 Karta orqali — chek yuborasiz, admin tekshiradi.",
        reply_markup=topup_payment_method_kb(),
    )


@router.callback_query(F.data == "wallet:pay:hamyon")
async def cb_pay_hamyon(callback: CallbackQuery, state: FSMContext, db_user):
    data = await state.get_data()
    amount = int(data.get("amount", 0))
    if not MIN_TOPUP <= amount <= MAX_TOPUP:
        await callback.answer("Summa eskirgan. Qaytadan boshlang.", show_alert=True)
        await state.clear()
        return

    await callback.answer("To'lov yaratilmoqda...")
    order_id = f"u{db_user['id']}-{uuid.uuid4().hex}"
    await state.clear()
    try:
        await services.create_hamyon_payment(db_user["id"], amount, order_id)
        result = await hamyon.create_payment(order_id, amount)
    except Exception:
        logger.exception("HamyonAPI orqali to'lov yaratilmadi")
        await services.fail_hamyon_payment(order_id)
        await callback.message.answer(
            "❌ Hozircha avtomatik to'lov yaratilmadi. Keyinroq urinib ko'ring "
            "yoki karta orqali to'lov usulini tanlang.",
            reply_markup=back_to_menu(),
        )
        return

    payment_id = str(result.get("payment_id"))
    card = result.get("card")
    expires_in = result.get("expires_in")
    lines = [
        "⚡ <b>HamyonAPI to'lovi yaratildi</b>",
        "",
        f"💰 Summa: <b>{fmt_sum(amount)}</b>",
        f"🧾 To'lov ID: <code>{esc(payment_id)}</code>",
    ]
    if card:
        lines.append(f"💳 Karta: <code>{esc(card)}</code>")
    if expires_in is not None:
        lines.append(f"⏳ Amal qilish muddati: {esc(expires_in)} soniya")
    lines.extend([
        "",
        "Ko'rsatilgan karta orqali aynan shu summani to'lang.",
        "To'lov tasdiqlangach balansingiz avtomatik to'ldiriladi. Chek yuborish shart emas.",
    ])
    await callback.message.answer("\n".join(lines), reply_markup=back_to_menu())


@router.callback_query(F.data == "wallet:pay:manual")
async def cb_pay_manual(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount = int(data.get("amount", 0))
    if not MIN_TOPUP <= amount <= MAX_TOPUP:
        await callback.answer("Summa eskirgan. Qaytadan boshlang.", show_alert=True)
        await state.clear()
        return

    await state.set_state(TopUp.receipt)
    await callback.answer()
    await callback.message.answer(
        "💳 <b>Karta orqali to'lov</b>\n\n"
        f"Summa: <b>{fmt_sum(amount)}</b>\n"
        f"Karta: {code(PAYMENT_CARD)}\n"
        f"Egasi: {esc(PAYMENT_HOLDER)}\n\n"
        "1️⃣ Kartaga shu summani o'tkazing.\n"
        "2️⃣ To'lov chekini yoki tranzaksiya ID sini yuboring.\n"
        "Admin tekshirib tasdiqlaydi.",
        reply_markup=cancel_kb(),
    )


@router.message(TopUp.receipt, F.photo | F.document | F.text)
async def topup_receipt(message: Message, state: FSMContext, db_user):
    data = await state.get_data()
    amount = int(data.get("amount", 0))
    if amount <= 0:
        await state.clear()
        await message.answer("❌ Ma'lumotlar eskirgan.", reply_markup=back_to_menu())
        return

    if message.photo:
        receipt_type = "photo"
        receipt_value = message.photo[-1].file_id
    elif message.document:
        receipt_type = "document"
        receipt_value = message.document.file_id
    else:
        receipt_type = "text"
        receipt_value = (message.text or "").strip()[:500]
        if len(receipt_value) < 3:
            await message.answer("❌ Chek ma'lumoti juda qisqa. Qaytadan yuboring.")
            return

    topup_id = await services.create_topup(
        db_user["id"], amount, receipt_type, receipt_value
    )
    await state.clear()
    await message.answer(
        "✅ <b>To'lov so'rovi yuborildi</b>\n\n"
        f"So'rov ID: {code(topup_id)}\n"
        f"Summa: <b>{fmt_sum(amount)}</b>\n\n"
        "Admin tekshirgach, balansingiz to'ladi. Bu odatda bir necha daqiqa "
        "vaqt oladi.",
        reply_markup=back_to_menu(),
    )

    topup = await services.get_topup_details(topup_id)
    if topup is not None:
        await notify_admin_topup(
            message.bot, topup, topup_review_kb(topup_id)
        )


@router.message(TopUp.receipt)
async def topup_receipt_wrong(message: Message):
    await message.answer(
        "🧾 Iltimos, chekni rasm, hujjat yoki tranzaksiya ID (matn) ko'rinishida yuboring."
    )


@router.callback_query(F.data == "wallet:history")
async def cb_wallet_history(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    transactions = await services.list_transactions(db_user["id"], limit=15)
    await callback.answer()
    if not transactions:
        await callback.message.answer(
            "🧾 Hozircha tranzaksiyalar yo'q.", reply_markup=back_to_menu()
        )
        return

    lines = ["🧾 <b>Oxirgi tranzaksiyalar</b>\n"]
    for tx in transactions:
        label = TYPE_LABELS.get(tx["type"], tx["type"])
        sign = "+" if tx["amount"] > 0 else ""
        lines.append(
            f"• {label}: <b>{sign}{fmt_sum(tx['amount'])}</b>\n"
            f"  {fmt_dt(tx['created_at'])}"
        )
    await callback.message.answer(
        "\n".join(lines), reply_markup=back_to_menu()
    )


@router.callback_query(F.data == "wallet:requests")
async def cb_wallet_requests(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    topups, withdrawals = await asyncio.gather(
        services.list_user_topups(db_user["id"], limit=10),
        services.list_user_withdrawals(db_user["id"], limit=10),
    )
    await callback.answer()
    if not topups and not withdrawals:
        await callback.message.answer(
            "📋 Hozircha so'rovlar yo'q.", reply_markup=wallet_menu()
        )
        return

    lines = ["📋 <b>So'rovlar tarixi</b>\n"]
    if topups:
        lines.append("<b>➕ Balans to'ldirish:</b>")
        for item in topups:
            status = REQUEST_STATUS_LABELS.get(item["status"], item["status"])
            lines.append(
                f"• #{item['id']} — {fmt_sum(item['amount'])} — {status} "
                f"({'HamyonAPI' if item.get('source') == 'hamyon' else 'Karta'}) "
                f"({fmt_dt(item['created_at'])})"
            )
    if withdrawals:
        if topups:
            lines.append("")
        lines.append("<b>💸 Pul yechish:</b>")
        for item in withdrawals:
            status = REQUEST_STATUS_LABELS.get(item["status"], item["status"])
            lines.append(
                f"• #{item['id']} — {fmt_sum(item['amount'])} — {status} "
                f"({fmt_dt(item['created_at'])})"
            )
    await callback.message.answer("\n".join(lines), reply_markup=wallet_menu())
