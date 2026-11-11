from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

import crypto
import services
from config import MIN_PRICE, MIN_WITHDRAW
from keyboards import (
    back_to_menu,
    cancel_kb,
    phone_keyboard,
    seller_account_detail,
    seller_accounts_kb,
    seller_menu,
)
from notifications import notify_admin_withdrawal
from states import AccountAdd, SellerAccount, SellerRegistration, Withdraw
from utils import (
    ACCOUNT_STATUS_LABELS,
    GAME_ACCOUNT_EXAMPLES,
    ORDER_STATUS_LABELS,
    clean_card,
    code,
    esc,
    fmt_dt,
    fmt_sum,
    mask_card,
    parse_amount,
)

router = Router(name="seller")


async def show_seller_menu(message: Message, db_user):
    user = await services.get_user(db_user["id"])
    stats = await services.seller_stats(user["id"])
    text = (
        "🛍 <b>Sotuvchi paneli</b>\n\n"
        f"🔑 Sotuvchi kodingiz: {code(user['seller_code'])}\n"
        f"💰 Balans: <b>{fmt_sum(user['balance'])}</b>\n"
        f"📦 Sotuvdagi: <b>{stats['active']}</b> ta\n"
        f"⏸ To'xtatilgan: <b>{stats['paused']}</b> ta\n"
        f"✅ Sotilgan: <b>{stats['sold']}</b> ta\n\n"
        "Xaridorlar sizni shu kod orqali topadi."
    )
    await message.answer(text, reply_markup=seller_menu())


@router.callback_query(F.data == "seller:register")
async def cb_register(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    user = await services.get_user(db_user["id"])
    if user["is_seller"]:
        await callback.answer("Siz allaqachon sotuvchisiz")
        await show_seller_menu(callback.message, db_user)
        return

    await callback.answer()
    await state.set_state(SellerRegistration.phone)
    await callback.message.answer(
        "📱 <b>Telefon raqamni tasdiqlang</b>\n\n"
        "Sotuvchi bo'lish uchun pastdagi tugma orqali telefon raqamingizni yuboring.\n"
        "Bu sizning haqiqiy foydalanuvchi ekanligingizni tasdiqlaydi.",
        reply_markup=phone_keyboard(),
    )


@router.message(SellerRegistration.phone, F.contact)
async def phone_received(message: Message, state: FSMContext, db_user):
    contact = message.contact
    if contact is None or contact.user_id != message.from_user.id:
        await message.answer("❌ Faqat o'zingizning telefon raqamingizni yuboring.")
        return

    phone = contact.phone_number or ""
    if len(clean_card(phone)) < 9:
        await message.answer("❌ Telefon raqam noto'g'ri. Qaytadan urinib ko'ring.")
        return

    try:
        user = await services.register_seller(db_user["id"], phone)
    except services.ServiceError as exc:
        await message.answer(f"❌ {esc(exc)}", reply_markup=ReplyKeyboardRemove())
        return

    await state.clear()
    await message.answer(
        "✅ <b>Telefon tasdiqlandi!</b>\n\n"
        "Siz muvaffaqiyatli sotuvchi sifatida ro'yxatdan o'tdingiz.\n"
        f"🔑 Sizning noyob sotuvchi kodingiz: {code(user['seller_code'])}\n\n"
        "Bu kodni xaridorlarga bering. Ular shu kod orqali akkauntlaringizni topadi.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await show_seller_menu(message, db_user)


@router.message(SellerRegistration.phone)
async def phone_wrong(message: Message):
    await message.answer("📱 Iltimos, pastdagi «Telefon raqamni yuborish» tugmasini bosing.")


@router.callback_query(F.data == "seller:menu")
async def cb_seller_menu(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    user = await services.get_user(db_user["id"])
    if not user["is_seller"]:
        await callback.answer("Avval sotuvchi bo'lib ro'yxatdan o'ting", show_alert=True)
        return
    await callback.answer()
    await show_seller_menu(callback.message, db_user)


@router.callback_query(F.data == "seller:add")
async def cb_add_account(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    user = await services.get_user(db_user["id"])
    if not user["is_seller"]:
        await callback.answer("Faqat sotuvchilar akkaunt qo'sha oladi", show_alert=True)
        return
    await callback.answer()
    await state.set_state(AccountAdd.login)
    await callback.message.answer(
        "➕ <b>Yangi akkaunt qo'shish</b>\n\n"
        f"🎮 {GAME_ACCOUNT_EXAMPLES}ni sotishingiz mumkin.\n\n"
        "1️⃣ Akkaunt loginini yuboring (username yoki telefon raqam).\n\n"
        "Login va parol shifrlangan holda saqlanadi.",
        reply_markup=cancel_kb(),
    )


@router.message(AccountAdd.login, F.text)
async def account_login(message: Message, state: FSMContext):
    login = message.text.strip()
    if not 2 <= len(login) <= 200:
        await message.answer("❌ Login 2 dan 200 belgigacha bo'lishi kerak. Qaytadan yuboring.")
        return
    await state.update_data(login_enc=crypto.encrypt(login))
    await state.set_state(AccountAdd.password)
    await message.answer(
        "2️⃣ Endi akkaunt parolini yuboring.\n\n"
        "⚠️ Xavfsizlik uchun bu xabarni keyin o'chirishingiz mumkin.",
        reply_markup=cancel_kb(),
    )


@router.message(AccountAdd.password, F.text)
async def account_password(message: Message, state: FSMContext):
    password = message.text.strip()
    if not 4 <= len(password) <= 200:
        await message.answer("❌ Parol 4 dan 200 belgigacha bo'lishi kerak. Qaytadan yuboring.")
        return
    await state.update_data(password_enc=crypto.encrypt(password))
    await state.set_state(AccountAdd.price)
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(
        "3️⃣ Akkaunt narxini so'mda kiriting (faqat raqam).\n\n"
        f"Masalan: <code>150000</code>\nMinimal narx: {fmt_sum(MIN_PRICE)}",
        reply_markup=cancel_kb(),
    )


@router.message(AccountAdd.price, F.text)
async def account_price(message: Message, state: FSMContext):
    price = parse_amount(message.text)
    if price is None or price < MIN_PRICE:
        await message.answer(
            f"❌ Narx noto'g'ri. Kamida {fmt_sum(MIN_PRICE)} kiriting (faqat raqam)."
        )
        return

    data = await state.get_data()
    if not data.get("login_enc") or not data.get("password_enc"):
        await state.clear()
        await message.answer("❌ Ma'lumotlar eskirgan. Qaytadan boshlang.", reply_markup=back_to_menu())
        return
    try:
        login = crypto.decrypt(data["login_enc"])
        password = crypto.decrypt(data["password_enc"])
    except ValueError:
        await state.clear()
        await message.answer("❌ Ma'lumotlar eskirgan. Qaytadan boshlang.", reply_markup=back_to_menu())
        return

    try:
        account_id = await services.create_account(
            db_user["id"], login, password, price
        )
    except services.ServiceError as exc:
        await state.clear()
        await message.answer(f"❌ {esc(exc)}", reply_markup=back_to_menu())
        return

    await state.clear()
    await message.answer(
        "✅ <b>E'lon joylandi!</b>\n\n"
        f"Akkaunt ID: {code(account_id)}\n"
        f"Narx: <b>{fmt_sum(price)}</b>\n\n"
        "E'lon darhol xaridorlarga ko'rinadi.",
    )
    await show_seller_menu(message, db_user)


@router.callback_query(F.data == "seller:accounts")
async def cb_accounts(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    user = await services.get_user(db_user["id"])
    if not user["is_seller"]:
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return

    accounts = await services.list_seller_accounts(user["id"])
    await callback.answer()
    if not accounts:
        await callback.message.answer(
            "📦 Hozircha akkauntlaringiz yo'q.\n«Akkaunt qo'shish» tugmasi orqali qo'shing.",
            reply_markup=seller_menu(),
        )
        return
    await callback.message.answer(
        f"📦 <b>Akkauntlaringiz</b> ({len(accounts)} ta)\n\n"
        "🟢 — sotuvda, ⏸ — to'xtatilgan, ✅ — sotilgan, ↩️ — qaytarilgan.\n"
        "Batafsil ma'lumot uchun akkauntni tanlang.",
        reply_markup=seller_accounts_kb(accounts[:40]),
    )


@router.callback_query(F.data.startswith("seller:acct:"))
async def cb_account_detail(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    try:
        account_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return

    account = await services.get_account(account_id)
    if account is None or account["seller_id"] != db_user["id"]:
        await callback.answer("Akkaunt topilmadi", show_alert=True)
        return

    await callback.answer()
    await _send_account_detail(callback.message, account)


async def _send_account_detail(message: Message, account) -> None:
    try:
        login = await services.get_account_login(account["id"])
    except services.ServiceError:
        login = "—"

    status = ACCOUNT_STATUS_LABELS.get(account["status"], account["status"])
    await message.answer(
        f"📦 <b>Akkaunt #{account['id']}</b>\n\n"
        f"🔑 Login: {code(login)}\n"
        "🔒 Parol: <code>••••••</code>\n"
        f"💰 Narx: <b>{fmt_sum(account['price'])}</b>\n"
        f"📌 Holat: {status}\n"
        f"📅 Qo'shilgan: {fmt_dt(account['created_at'])}",
        reply_markup=seller_account_detail(account),
        protect_content=True,
    )


@router.callback_query(F.data.startswith("seller:pause:"))
@router.callback_query(F.data.startswith("seller:resume:"))
async def cb_toggle_account(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    try:
        account_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return

    paused = callback.data.startswith("seller:pause:")
    try:
        await services.set_account_paused(account_id, db_user["id"], paused)
    except services.ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    account = await services.get_account(account_id)
    await callback.answer("To'xtatildi" if paused else "Davom ettirildi")
    if account is not None:
        await _send_account_detail(callback.message, account)


@router.callback_query(F.data.startswith("seller:editprice:"))
async def cb_edit_price(callback: CallbackQuery, state: FSMContext, db_user):
    try:
        account_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return

    account = await services.get_account(account_id)
    if account is None or account["seller_id"] != db_user["id"]:
        await callback.answer("Akkaunt topilmadi", show_alert=True)
        return
    if account["status"] not in ("active", "paused"):
        await callback.answer("Bu akkaunt narxini o'zgartirib bo'lmaydi", show_alert=True)
        return

    await state.set_state(SellerAccount.edit_price)
    await state.update_data(account_id=account_id)
    await callback.answer()
    await callback.message.answer(
        f"✏️ <b>#{account_id}</b> akkaunt uchun yangi narxni kiriting.\n\n"
        f"Joriy narx: <b>{fmt_sum(account['price'])}</b>\n"
        f"Minimal narx: {fmt_sum(MIN_PRICE)}",
        reply_markup=cancel_kb(f"seller:acct:{account_id}"),
    )


@router.message(SellerAccount.edit_price, F.text)
async def edit_price_value(message: Message, state: FSMContext, db_user):
    price = parse_amount(message.text)
    if price is None or price < MIN_PRICE:
        await message.answer(
            f"❌ Narx noto'g'ri. Kamida {fmt_sum(MIN_PRICE)} kiriting (faqat raqam)."
        )
        return

    data = await state.get_data()
    account_id = int(data.get("account_id", 0))
    if not account_id:
        await state.clear()
        await message.answer("❌ Ma'lumotlar eskirgan.", reply_markup=back_to_menu())
        return

    try:
        await services.update_account_price(account_id, db_user["id"], price)
    except services.ServiceError as exc:
        await message.answer(f"❌ {esc(exc)}")
        return

    await state.clear()
    account = await services.get_account(account_id)
    await message.answer(f"✅ Narx yangilandi: <b>{fmt_sum(price)}</b>")
    if account is not None:
        await _send_account_detail(message, account)


@router.message(SellerAccount.edit_price)
async def edit_price_wrong(message: Message):
    await message.answer("✏️ Iltimos, yangi narxni faqat raqam bilan yuboring.")


@router.callback_query(F.data.startswith("seller:del:"))
async def cb_delete_account(callback: CallbackQuery, db_user):
    try:
        account_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return

    account = await services.get_account(account_id)
    if account is None or account["seller_id"] != db_user["id"]:
        await callback.answer("Akkaunt topilmadi", show_alert=True)
        return
    await callback.answer()
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    await callback.message.answer(
        f"🗑 <b>#{account_id}</b> akkauntni o'chirishni tasdiqlaysizmi?\n"
        "Bu amalni qaytarib bo'lmaydi.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Ha, o'chirish", callback_data=f"seller:delok:{account_id}")],
                [InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"seller:acct:{account_id}")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("seller:delok:"))
async def cb_delete_account_confirm(callback: CallbackQuery, db_user):
    try:
        account_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return
    try:
        await services.delete_account(account_id, db_user["id"])
    except services.ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.answer("O'chirildi")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("✅ Akkaunt o'chirildi.")


@router.callback_query(F.data == "seller:sales")
async def cb_sales(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    user = await services.get_user(db_user["id"])
    if not user["is_seller"]:
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    sales = await services.list_seller_sales(user["id"], limit=15)
    await callback.answer()
    if not sales:
        await callback.message.answer(
            "🧾 Hozircha savdolar yo'q.", reply_markup=back_to_menu()
        )
        return

    lines = ["🧾 <b>Oxirgi savdolar</b>\n"]
    for order in sales:
        status = ORDER_STATUS_LABELS.get(order["status"], order["status"])
        total = order["price"] + order["penalty"]
        lines.append(
            f"• <b>#{order['id']}</b> | {status}\n"
            f"  💰 {fmt_sum(total)} | jarima: {fmt_sum(order['penalty'])}\n"
            f"  👤 {esc(order['buyer_name'])} | {fmt_dt(order['created_at'])}"
        )
    await callback.message.answer("\n".join(lines), reply_markup=back_to_menu())


@router.callback_query(F.data == "seller:withdraw")
async def cb_withdraw(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    user = await services.get_user(db_user["id"])
    if not user["is_seller"]:
        await callback.answer("Faqat sotuvchilar pul yechadi", show_alert=True)
        return
    await callback.answer()
    await state.set_state(Withdraw.amount)
    await callback.message.answer(
        "💳 <b>Pul yechish</b>\n\n"
        f"Mavjud balans: <b>{fmt_sum(user['balance'])}</b>\n"
        f"Minimal summa: {fmt_sum(MIN_WITHDRAW)}\n\n"
        "Yechib olmoqchi bo'lgan summani kiriting:",
        reply_markup=cancel_kb(),
    )


@router.message(Withdraw.amount, F.text)
async def withdraw_amount(message: Message, state: FSMContext, db_user):
    amount = parse_amount(message.text)
    user = await services.get_user(db_user["id"])
    if amount is None or amount < MIN_WITHDRAW:
        await message.answer(f"❌ Kamida {fmt_sum(MIN_WITHDRAW)} kiriting.")
        return
    if amount > user["balance"]:
        await message.answer(
            f"❌ Balansda yetarli mablag' yo'q. Mavjud: {fmt_sum(user['balance'])}"
        )
        return
    await state.update_data(amount=amount)
    await state.set_state(Withdraw.card)
    await message.answer(
        "💳 Karta raqamingizni kiriting (16 raqam).\n\nMasalan: <code>8600123456789012</code>",
        reply_markup=cancel_kb(),
    )


@router.message(Withdraw.card, F.text)
async def withdraw_card(message: Message, state: FSMContext, db_user):
    card = clean_card(message.text)
    if not 13 <= len(card) <= 19:
        await message.answer("❌ Karta raqami noto'g'ri. 13-19 raqam kiriting.")
        return

    data = await state.get_data()
    amount = int(data.get("amount", 0))
    try:
        withdrawal_id = await services.create_withdrawal(
            db_user["id"], amount, card
        )
    except services.InsufficientFunds as exc:
        await state.clear()
        await message.answer(f"❌ {esc(exc)}", reply_markup=back_to_menu())
        return
    except services.ServiceError as exc:
        await state.clear()
        await message.answer(f"❌ {esc(exc)}", reply_markup=back_to_menu())
        return

    await state.clear()
    await message.answer(
        "✅ <b>So'rov qabul qilindi</b>\n\n"
        f"So'rov ID: {code(withdrawal_id)}\n"
        f"Summa: <b>{fmt_sum(amount)}</b>\n"
        f"Karta: {code(mask_card(card))}\n\n"
        "Admin tekshirgach, mablag' kartangizga o'tkaziladi."
    )

    withdrawal = await services.get_withdrawal(withdrawal_id)
    if withdrawal is not None:
        from keyboards import withdrawal_review_kb

        await notify_admin_withdrawal(
            message.bot, withdrawal, withdrawal_review_kb(withdrawal_id)
        )
    await show_seller_menu(message, db_user)
