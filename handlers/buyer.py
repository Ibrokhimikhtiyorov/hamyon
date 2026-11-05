from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import services
from config import (
    CHECK_MINUTES,
    COMMISSION_PERCENT,
    PENALTY_INTERVAL_MINUTES,
    PENALTY_PERCENT,
    SELLER_SHARE_PERCENT,
)
from keyboards import (
    account_offer_kb,
    back_to_menu,
    buyer_accounts_kb,
    cancel_kb,
    dispute_review_kb,
    order_actions,
    order_detail_kb,
)
from notifications import notify_admin_dispute
from states import DisputeFlow, SellerSearch
from utils import (
    ORDER_STATUS_LABELS,
    code,
    esc,
    fmt_dt,
    fmt_sum,
    normalize_seller_code,
    remaining_text,
)

router = Router(name="buyer")


@router.callback_query(F.data == "buyer:search")
async def cb_search(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    await callback.answer()
    await state.set_state(SellerSearch.code)
    await callback.message.answer(
        "🔎 <b>Sotuvchini qidirish</b>\n\n"
        "Sotuvchining noyob ID kodini kiriting.\n"
        "Masalan: <code>AB12CD34</code>",
        reply_markup=cancel_kb(),
    )


@router.message(SellerSearch.code, F.text)
async def search_seller(message: Message, state: FSMContext, db_user):
    seller_code = normalize_seller_code(message.text)
    if not 4 <= len(seller_code) <= 16:
        await message.answer("❌ Kod noto'g'ri. Qaytadan kiriting.")
        return

    seller = await services.get_user_by_code(seller_code)
    if seller is None:
        await message.answer("❌ Bunday kodli sotuvchi topilmadi. Kodni tekshiring.")
        return

    accounts = await services.list_active_accounts(seller["id"])
    await state.clear()
    if not accounts:
        await message.answer(
            f"👤 <b>{esc(seller['full_name'])}</b>\n"
            f"🔑 Kod: {code(seller['seller_code'])}\n\n"
            "📦 Hozircha sotuvda akkaunt yo'q.",
            reply_markup=back_to_menu(),
        )
        return

    await message.answer(
        f"👤 <b>Sotuvchi topildi</b>\n\n"
        f"🔑 Kod: {code(seller['seller_code'])}\n"
        f"📦 Sotuvdagi akkauntlar: <b>{len(accounts)}</b> ta\n\n"
        "Akkauntni tanlab, narxi bilan tanishing:",
        reply_markup=buyer_accounts_kb(accounts[:40]),
    )


@router.callback_query(F.data.startswith("shop:acct:"))
async def cb_shop_account(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    try:
        account_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return

    account = await services.get_account(account_id)
    if account is None or account["status"] != "active":
        await callback.answer("Bu akkaunt sotuvda emas", show_alert=True)
        return

    seller = await services.get_user(account["seller_id"])
    buyer = await services.get_user(db_user["id"])
    await callback.answer()
    await callback.message.answer(
        f"📦 <b>Akkaunt #{account['id']}</b>\n\n"
        f"👤 Sotuvchi: {esc(seller['full_name'])}\n"
        f"🔑 Sotuvchi kodi: {code(seller['seller_code'])}\n"
        f"💰 Narx: <b>{fmt_sum(account['price'])}</b>\n\n"
        "To'lov hamyoningizdan yechiladi. To'lovdan so'ng login-parol ochiladi va "
        f"tekshirish uchun {CHECK_MINUTES} daqiqa vaqt beriladi.\n\n"
        f"💳 Balansingiz: <b>{fmt_sum(buyer['balance'])}</b>",
        reply_markup=account_offer_kb(account["id"], buyer["balance"], account["price"]),
    )


@router.callback_query(F.data.startswith("shop:confirm:"))
async def cb_buy_confirm(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    try:
        account_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return

    account = await services.get_account(account_id)
    if account is None or account["status"] != "active":
        await callback.answer("Bu akkaunt allaqachon sotilgan", show_alert=True)
        return

    buyer = await services.get_user(db_user["id"])
    if buyer["balance"] < account["price"]:
        await callback.answer("Balans yetarli emas", show_alert=True)
        return

    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        "🧾 <b>Xaridni tasdiqlang</b>\n\n"
        f"📦 Akkaunt: <b>#{account['id']}</b>\n"
        f"💰 Narx: <b>{fmt_sum(account['price'])}</b>\n"
        f"👛 Balansingiz: <b>{fmt_sum(buyer['balance'])}</b>\n\n"
        "Tasdiqlaganingizdan so'ng mablag' escrow'ga o'tkaziladi va login-parol "
        "ochiladi. Bu amalni bekor qilib bo'lmaydi.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Tasdiqlash va sotib olish",
                        callback_data=f"shop:buy:{account_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Bekor qilish",
                        callback_data=f"shop:acct:{account_id}",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("shop:buy:"))
async def cb_buy(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    try:
        account_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return

    try:
        order, account, seller = await services.create_order(db_user["id"], account_id)
    except services.InsufficientFunds as exc:
        await callback.answer("Balans yetarli emas", show_alert=True)
        await callback.message.answer(
            f"❌ {esc(exc)}\n\nBalansni to'ldirib, qaytadan urinib ko'ring.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Balansni to'ldirish", callback_data="wallet:topup")],
                    [InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="nav:menu")],
                ]
            ),
        )
        return
    except services.ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    try:
        login, password = await services.get_credentials(order["id"], db_user["id"])
    except services.ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.answer("To'lov amalga oshirildi")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await callback.message.answer(
        "✅ <b>Xarid muvaffaqiyatli!</b>\n\n"
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

    try:
        await callback.bot.send_message(
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


@router.callback_query(F.data.startswith("order:confirm:"))
async def cb_confirm_order(callback: CallbackQuery, db_user):
    try:
        order_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return

    try:
        info = await services.confirm_order(order_id, db_user["id"])
    except services.ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.answer("Tasdiqlandi")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        "✅ <b>Buyurtma yakunlandi!</b>\n\n"
        f"Buyurtma: <b>#{info['order_id']}</b>\n"
        f"Jarima: {fmt_sum(info['penalty'])}\n"
        f"Jami: <b>{fmt_sum(info['total'])}</b>\n\n"
        "Xaridingiz uchun rahmat!",
        reply_markup=back_to_menu(),
    )

    if info["seller_tg"]:
        try:
            await callback.bot.send_message(
                info["seller_tg"],
                "💰 <b>Savdo yakunlandi!</b>\n\n"
                f"Buyurtma: <b>#{info['order_id']}</b>\n"
                f"Balansingizga qo'shildi: <b>{fmt_sum(info['seller_share'])}</b> "
                f"({SELLER_SHARE_PERCENT}%)\n"
                f"Bot komissiyasi: {fmt_sum(info['commission'])} ({COMMISSION_PERCENT}%)",
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("order:dispute:"))
async def cb_dispute(callback: CallbackQuery, state: FSMContext, db_user):
    try:
        order_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return

    order = await services.get_order(order_id)
    if order is None or order["buyer_id"] != db_user["id"]:
        await callback.answer("Buyurtma topilmadi", show_alert=True)
        return
    if order["status"] != "checking":
        await callback.answer("Bu buyurtma bo'yicha shikoyat ochib bo'lmaydi", show_alert=True)
        return

    await state.set_state(DisputeFlow.reason)
    await state.update_data(order_id=order_id)
    await callback.answer()
    await callback.message.answer(
        "⚠️ <b>Shikoyat qoldirish</b>\n\n"
        f"Buyurtma: <b>#{order_id}</b>\n\n"
        "Muammoni qisqa va aniq yozing (masalan: login ishlamayapti, parol noto'g'ri).\n"
        "Shikoyat admin tomonidan ko'rib chiqiladi.",
        reply_markup=cancel_kb(),
    )


@router.message(DisputeFlow.reason, F.text)
async def dispute_reason(message: Message, state: FSMContext, db_user):
    data = await state.get_data()
    order_id = int(data.get("order_id", 0))
    if not order_id:
        await state.clear()
        await message.answer("❌ Ma'lumotlar eskirgan.", reply_markup=back_to_menu())
        return

    try:
        await services.create_dispute(order_id, db_user["id"], message.text)
    except services.ServiceError as exc:
        await message.answer(f"❌ {esc(exc)}")
        return

    await state.clear()
    dispute = await services.get_dispute_details(order_id)
    await message.answer(
        "✅ <b>Shikoyatingiz qabul qilindi</b>\n\n"
        f"Buyurtma: <b>#{order_id}</b>\n"
        "Admin tez orada ko'rib chiqadi va javob beradi.\n"
        "Shikoyat ko'rib chiqilguncha jarima hisoblanmaydi.",
        reply_markup=back_to_menu(),
    )

    if dispute is not None:
        await notify_admin_dispute(
            message.bot, dispute, dispute_review_kb(order_id)
        )
        try:
            await message.bot.send_message(
                dispute["seller_tg"],
                "⚠️ <b>Xaridor shikoyat qoldirdi</b>\n\n"
                f"Buyurtma: <b>#{order_id}</b>\n"
                f"Akkaunt: #{dispute['account_id']}\n"
                f"Sabab: {esc(dispute['reason'])}\n\n"
                "Admin ko'rib chiqadi.",
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("order:creds:"))
async def cb_order_creds(callback: CallbackQuery, db_user):
    try:
        order_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return

    try:
        login, password = await services.get_credentials(order_id, db_user["id"])
    except services.ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.answer()
    await callback.message.answer(
        "🔑 <b>Akkaunt ma'lumotlari</b>\n\n"
        f"Buyurtma: <b>#{order_id}</b>\n"
        f"Login: {code(login)}\n"
        f"Parol: {code(password)}\n\n"
        "⚠️ Bu ma'lumotlarni boshqalarga bermang.",
        protect_content=True,
    )


@router.callback_query(F.data.startswith("order:view:"))
async def cb_order_view(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    try:
        order_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return

    order = await services.get_order_with_people(order_id)
    if order is None or order["buyer_id"] != db_user["id"]:
        await callback.answer("Buyurtma topilmadi", show_alert=True)
        return

    status = ORDER_STATUS_LABELS.get(order["status"], order["status"])
    total = order["price"] + order["penalty"]
    text = (
        f"🧾 <b>Buyurtma #{order['id']}</b>\n\n"
        f"📦 Akkaunt: #{order['account_id']}\n"
        f"👤 Sotuvchi: {esc(order['seller_name'])} "
        f"({code(order['seller_code'])})\n"
        f"💰 Narx: {fmt_sum(order['price'])}\n"
        f"⏱ Jarima: {fmt_sum(order['penalty'])}\n"
        f"💵 Jami: <b>{fmt_sum(total)}</b>\n"
        f"📌 Holat: <b>{status}</b>\n"
        f"🕐 Yaratilgan: {fmt_dt(order['created_at'])}\n"
    )
    if order["status"] == "checking":
        text += (
            f"⏰ Tekshirish: {fmt_dt(order['check_deadline'])} gacha "
            f"({remaining_text(order['check_deadline'])})\n"
        )
    if order["completed_at"]:
        text += f"✅ Yakunlangan: {fmt_dt(order['completed_at'])}\n"

    await callback.answer()
    await callback.message.answer(
        text, reply_markup=order_detail_kb(order["id"], order["status"])
    )


@router.callback_query(F.data == "buyer:history")
async def cb_history(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    orders = await services.list_buyer_orders(db_user["id"], limit=10)
    await callback.answer()
    if not orders:
        await callback.message.answer(
            "📜 Hozircha xaridlar yo'q.", reply_markup=back_to_menu()
        )
        return

    rows = []
    lines = ["📜 <b>Oxirgi xaridlar</b>\n"]
    for order in orders:
        status = ORDER_STATUS_LABELS.get(order["status"], order["status"])
        total = order["price"] + order["penalty"]
        lines.append(
            f"• <b>#{order['id']}</b> | {status}\n"
            f"  💰 {fmt_sum(total)} | {fmt_dt(order['created_at'])}"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"#{order['id']} — {status}",
                    callback_data=f"order:view:{order['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="nav:menu")])
    await callback.message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
