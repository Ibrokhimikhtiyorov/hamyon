import asyncio

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import services
from keyboards import (
    admin_menu,
    admin_users_kb,
    broadcast_confirm_kb,
    dispute_review_kb,
    topup_review_kb,
    withdrawal_review_kb,
)
from notifications import (
    dispute_text,
    send_topup_card,
    withdrawal_text,
)
from states import AdminFlow
from utils import (
    ORDER_STATUS_LABELS,
    code,
    esc,
    fmt_dt,
    fmt_sum,
    parse_amount,
)

router = Router(name="admin")


def _is_admin(db_user) -> bool:
    return bool(db_user and db_user["is_admin"])


async def _deny(callback: CallbackQuery) -> None:
    await callback.answer("Ruxsat yo'q", show_alert=True)


@router.callback_query(F.data == "admin:menu")
async def cb_admin_menu(callback: CallbackQuery, state: FSMContext, db_user):
    if not _is_admin(db_user):
        await _deny(callback)
        return
    await state.clear()
    await callback.answer()
    await callback.message.answer(
        "🛠 <b>Admin panel</b>\n\nKerakli bo'limni tanlang:",
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data == "admin:stats")
async def cb_stats(callback: CallbackQuery, db_user):
    if not _is_admin(db_user):
        await _deny(callback)
        return
    stats = await services.admin_stats()
    await callback.answer()
    await callback.message.answer(
        "📊 <b>Platforma statistikasi</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{stats['users']}</b>\n"
        f"🧑‍💼 Sotuvchilar: <b>{stats['sellers']}</b>\n"
        f"📦 Sotuvdagi akkauntlar: <b>{stats['active_accounts']}</b>\n"
        f"✅ Sotilgan akkauntlar: <b>{stats['sold_accounts']}</b>\n\n"
        f"⏳ Tekshirilayotgan buyurtmalar: <b>{stats['checking']}</b>\n"
        f"🏁 Yakunlangan savdolar: <b>{stats['completed']}</b>\n"
        f"⚠️ Faol shikoyatlar: <b>{stats['disputed']}</b>\n\n"
        f"💵 Umumiy savdo hajmi: <b>{fmt_sum(stats['gmv'])}</b>\n"
        f"🏦 Bot komissiyasi: <b>{fmt_sum(stats['commission'])}</b>\n\n"
        f"💳 Kutilayotgan to'lovlar: <b>{stats['pending_topups']}</b>\n"
        f"💸 Kutilayotgan pul yechish: <b>{stats['pending_withdrawals']}</b>",
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data == "admin:audit")
async def cb_audit(callback: CallbackQuery, db_user):
    if not _is_admin(db_user):
        await _deny(callback)
        return
    logs = await services.list_audit_logs(limit=20)
    await callback.answer()
    if not logs:
        await callback.message.answer(
            "📝 Audit jurnal hozircha bo'sh.", reply_markup=admin_menu()
        )
        return

    lines = ["📝 <b>Oxirgi admin amallari</b>\n"]
    for entry in logs:
        actor = entry["actor_tg"] if entry["actor_tg"] else "-"
        details = (entry["details"] or "").strip()
        lines.append(
            f"• <b>{esc(entry['action'])}</b> | {code(actor)}\n"
            f"  {esc(details[:200])}\n"
            f"  {fmt_dt(entry['created_at'])}"
        )
    await callback.message.answer("\n".join(lines), reply_markup=admin_menu())


@router.callback_query(F.data == "admin:topups")
async def cb_topups(callback: CallbackQuery, db_user):
    if not _is_admin(db_user):
        await _deny(callback)
        return
    topups = await services.list_pending_topups(limit=10)
    await callback.answer()
    if not topups:
        await callback.message.answer(
            "✅ Kutilayotgan to'lovlar yo'q.", reply_markup=admin_menu()
        )
        return
    await callback.message.answer(f"💳 <b>Kutilayotgan to'lovlar</b> ({len(topups)} ta)")
    for topup in topups:
        await send_topup_card(
            callback.bot,
            callback.message.chat.id,
            topup,
            topup_review_kb(topup["id"]),
        )


@router.callback_query(F.data == "admin:withdrawals")
async def cb_withdrawals(callback: CallbackQuery, db_user):
    if not _is_admin(db_user):
        await _deny(callback)
        return
    withdrawals = await services.list_pending_withdrawals(limit=10)
    await callback.answer()
    if not withdrawals:
        await callback.message.answer(
            "✅ Kutilayotgan pul yechish so'rovlari yo'q.", reply_markup=admin_menu()
        )
        return
    await callback.message.answer(
        f"💸 <b>Kutilayotgan pul yechish so'rovlari</b> ({len(withdrawals)} ta)"
    )
    for withdrawal in withdrawals:
        await callback.message.answer(
            withdrawal_text(withdrawal),
            reply_markup=withdrawal_review_kb(withdrawal["id"]),
        )


@router.callback_query(F.data == "admin:disputes")
async def cb_disputes(callback: CallbackQuery, db_user):
    if not _is_admin(db_user):
        await _deny(callback)
        return
    disputes = await services.list_pending_disputes(limit=10)
    await callback.answer()
    if not disputes:
        await callback.message.answer(
            "✅ Faol shikoyatlar yo'q.", reply_markup=admin_menu()
        )
        return
    await callback.message.answer(f"⚠️ <b>Faol shikoyatlar</b> ({len(disputes)} ta)")
    for dispute in disputes:
        await callback.message.answer(
            dispute_text(dispute),
            reply_markup=dispute_review_kb(dispute["order_id"]),
        )


@router.callback_query(F.data == "admin:sales")
async def cb_sales(callback: CallbackQuery, db_user):
    if not _is_admin(db_user):
        await _deny(callback)
        return
    sales = await services.list_recent_sales(limit=20)
    await callback.answer()
    if not sales:
        await callback.message.answer(
            "🧾 Hozircha savdolar yo'q.", reply_markup=admin_menu()
        )
        return

    lines = ["🧾 <b>Oxirgi savdolar</b>\n"]
    for order in sales:
        status = ORDER_STATUS_LABELS.get(order["status"], order["status"])
        total = order["price"] + order["penalty"]
        lines.append(
            f"• <b>#{order['id']}</b> | {status} | {fmt_sum(total)}\n"
            f"  Sotuvchi: {esc(order['seller_name'])} ({code(order['seller_code'])})\n"
            f"  Xaridor: {esc(order['buyer_name'])} | {fmt_dt(order['created_at'])}"
        )
    await callback.message.answer("\n".join(lines), reply_markup=admin_menu())


@router.callback_query(F.data == "admin:users")
async def cb_users(callback: CallbackQuery, state: FSMContext, db_user):
    if not _is_admin(db_user):
        await _deny(callback)
        return
    await state.set_state(AdminFlow.user_query)
    await callback.answer()
    await callback.message.answer(
        "👤 <b>Foydalanuvchini qidirish</b>\n\n"
        "Telegram ID, foydalanuvchi ID, sotuvchi kodi yoki username yuboring:",
        reply_markup=admin_menu(),
    )


@router.message(AdminFlow.user_query, F.text)
async def user_query(message: Message, state: FSMContext, db_user):
    if not _is_admin(db_user):
        return
    user = await services.search_user(message.text)
    if user is None:
        await message.answer("❌ Foydalanuvchi topilmadi.")
        return

    await state.clear()
    role = "Sotuvchi" if user["is_seller"] else "Xaridor"
    if user["is_admin"]:
        role += " + Admin"
    text = (
        "👤 <b>Foydalanuvchi</b>\n\n"
        f"Baza ID: {code(user['id'])}\n"
        f"Telegram ID: {code(user['tg_id'])}\n"
        f"Ism: {esc(user['full_name'])}\n"
        f"Username: @{esc(user['username'] or 'yo`q')}\n"
        f"Telefon: {esc(user['phone'] or '-')}\n"
        f"Rol: {role}\n"
        f"Sotuvchi kodi: {code(user['seller_code'] or '-')}\n"
        f"Balans: <b>{fmt_sum(user['balance'])}</b>\n"
        f"Holat: {'🚫 Bloklangan' if user['is_blocked'] else '✅ Faol'}\n"
        f"Ro'yxatdan o'tgan: {fmt_dt(user['created_at'])}"
    )
    await message.answer(text, reply_markup=admin_users_kb(user))


@router.callback_query(F.data.startswith("admin:bal:"))
async def cb_balance_adjust(callback: CallbackQuery, state: FSMContext, db_user):
    if not _is_admin(db_user):
        await _deny(callback)
        return
    try:
        user_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return

    user = await services.get_user(user_id)
    if user is None:
        await callback.answer("Foydalanuvchi topilmadi", show_alert=True)
        return

    await state.set_state(AdminFlow.balance_amount)
    await state.update_data(balance_user_id=user_id)
    await callback.answer()
    await callback.message.answer(
        "💰 <b>Balansni o'zgartirish</b>\n\n"
        f"Foydalanuvchi: {esc(user['full_name'])} ({code(user['tg_id'])})\n"
        f"Joriy balans: <b>{fmt_sum(user['balance'])}</b>\n\n"
        "Summani kiriting. Qo'shish uchun oddiy raqam, ayirish uchun oldiga "
        "minus qo'ying.\nMasalan: <code>50000</code> yoki <code>-25000</code>",
        reply_markup=admin_menu(),
    )


@router.message(AdminFlow.balance_amount, F.text)
async def balance_amount(message: Message, state: FSMContext, db_user):
    if not _is_admin(db_user):
        return
    raw = message.text.strip()
    amount = parse_amount(raw)
    if amount is None or amount == 0:
        await message.answer("❌ Summa noto'g'ri. Masalan: <code>50000</code>")
        return
    if raw.startswith("-"):
        amount = -amount

    await state.update_data(balance_amount=amount)
    await state.set_state(AdminFlow.balance_note)
    await message.answer(
        f"Summa: <b>{fmt_sum(amount)}</b>\n\n"
        "Izoh yozing yoki <code>-</code> yuboring.",
    )


@router.message(AdminFlow.balance_note, F.text)
async def balance_note(message: Message, state: FSMContext, db_user):
    if not _is_admin(db_user):
        return
    data = await state.get_data()
    user_id = int(data.get("balance_user_id", 0))
    amount = int(data.get("balance_amount", 0))
    if not user_id or not amount:
        await state.clear()
        await message.answer("❌ Ma'lumotlar eskirgan.", reply_markup=admin_menu())
        return

    note = message.text.strip()
    if note == "-":
        note = ""
    try:
        new_balance = await services.adjust_balance(
            user_id, amount, db_user["tg_id"], note
        )
    except services.ServiceError as exc:
        await state.clear()
        await message.answer(f"❌ {esc(exc)}", reply_markup=admin_menu())
        return

    await state.clear()
    await message.answer(
        "✅ <b>Balans o'zgartirildi</b>\n\n"
        f"O'zgarish: <b>{fmt_sum(amount)}</b>\n"
        f"Yangi balans: <b>{fmt_sum(new_balance)}</b>",
        reply_markup=admin_menu(),
    )


@router.message(AdminFlow.balance_amount)
async def balance_amount_wrong(message: Message, db_user):
    if not _is_admin(db_user):
        return
    await message.answer("💰 Iltimos, summani faqat raqam bilan yuboring.")


@router.message(AdminFlow.balance_note)
async def balance_note_wrong(message: Message, db_user):
    if not _is_admin(db_user):
        return
    await message.answer("📝 Iltimos, izohni matn ko'rinishida yuboring yoki <code>-</code>.")


@router.callback_query(F.data.startswith("admin:block:"))
async def cb_block(callback: CallbackQuery, db_user):
    if not _is_admin(db_user):
        await _deny(callback)
        return
    try:
        user_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return
    user = await services.get_user(user_id)
    if user is None:
        await callback.answer("Foydalanuvchi topilmadi", show_alert=True)
        return
    await services.set_blocked(user_id, True)
    await services.log_audit(
        db_user["tg_id"], "user_blocked", f"user={user_id} tg={user['tg_id']}"
    )
    await callback.answer("Bloklandi")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(f"🚫 Foydalanuvchi bloklandi: {code(user['tg_id'])}")


@router.callback_query(F.data.startswith("admin:unblock:"))
async def cb_unblock(callback: CallbackQuery, db_user):
    if not _is_admin(db_user):
        await _deny(callback)
        return
    try:
        user_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return
    user = await services.get_user(user_id)
    if user is None:
        await callback.answer("Foydalanuvchi topilmadi", show_alert=True)
        return
    await services.set_blocked(user_id, False)
    await services.log_audit(
        db_user["tg_id"], "user_unblocked", f"user={user_id} tg={user['tg_id']}"
    )
    await callback.answer("Blokdan chiqarildi")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        f"✅ Foydalanuvchi blokdan chiqarildi: {code(user['tg_id'])}"
    )


@router.callback_query(F.data == "admin:broadcast")
async def cb_broadcast(callback: CallbackQuery, state: FSMContext, db_user):
    if not _is_admin(db_user):
        await _deny(callback)
        return
    await state.set_state(AdminFlow.broadcast_text)
    await callback.answer()
    await callback.message.answer(
        "📢 <b>Ommaviy xabar</b>\n\n"
        "Barcha faol foydalanuvchilarga yuboriladigan xabarni kiriting.\n"
        "Xabar oddiy matn sifatida yuboriladi.",
        reply_markup=admin_menu(),
    )


@router.message(AdminFlow.broadcast_text, F.text)
async def broadcast_text(message: Message, state: FSMContext, db_user):
    if not _is_admin(db_user):
        return
    text = message.text.strip()
    if len(text) < 2:
        await message.answer("❌ Xabar juda qisqa.")
        return
    await state.update_data(broadcast_text=text)
    await state.set_state(AdminFlow.broadcast_confirm)
    await message.answer(
        "📢 <b>Xabar ko'rinishi</b>\n\n"
        f"{esc(text)}\n\n"
        "Barcha faol foydalanuvchilarga yuborilsinmi?",
        reply_markup=broadcast_confirm_kb(),
    )


@router.callback_query(F.data == "admin:bcast:go")
async def broadcast_go(callback: CallbackQuery, state: FSMContext, db_user):
    if not _is_admin(db_user):
        await _deny(callback)
        return
    data = await state.get_data()
    text = data.get("broadcast_text")
    if not text:
        await state.clear()
        await callback.answer("Xabar topilmadi", show_alert=True)
        return

    await state.clear()
    await callback.answer("Yuborilmoqda...")
    await services.log_audit(db_user["tg_id"], "broadcast", text[:200])
    recipients = await services.broadcast_recipients()
    sent = 0
    failed = 0
    for row in recipients:
        try:
            await callback.bot.send_message(row["tg_id"], text, parse_mode=None)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await callback.message.answer(
        "📢 <b>Xabar yuborildi</b>\n\n"
        f"✅ Yetkazildi: {sent}\n"
        f"❌ Xatolik: {failed}",
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data.startswith("admin:topup:"))
async def cb_topup_review(callback: CallbackQuery, db_user):
    if not _is_admin(db_user):
        await _deny(callback)
        return
    parts = callback.data.split(":")
    try:
        action = parts[2]
        topup_id = int(parts[3])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return

    try:
        result = await services.review_topup(
            topup_id, action == "approve", db_user["tg_id"]
        )
    except services.ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await services.log_audit(
        db_user["tg_id"],
        "topup_approved" if result["approved"] else "topup_rejected",
        f"topup={topup_id} amount={result['amount']}",
    )
    await callback.answer("Tasdiqlandi" if result["approved"] else "Rad etildi")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    if result["approved"]:
        user_text = (
            "✅ <b>Balans to'ldirildi!</b>\n\n"
            f"Summa: <b>{fmt_sum(result['amount'])}</b>\n"
            "Mablag' hamyoningizga qo'shildi."
        )
    else:
        user_text = (
            "❌ <b>To'lov rad etildi</b>\n\n"
            f"Summa: {fmt_sum(result['amount'])}\n"
            "Chek tekshiruvdan o'tmadi. Admin bilan bog'laning."
        )
    if result["user_tg"]:
        try:
            await callback.bot.send_message(result["user_tg"], user_text)
        except Exception:
            pass


@router.callback_query(F.data.startswith("admin:wd:"))
async def cb_withdrawal_review(callback: CallbackQuery, db_user):
    if not _is_admin(db_user):
        await _deny(callback)
        return
    parts = callback.data.split(":")
    try:
        action = parts[2]
        withdrawal_id = int(parts[3])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return

    try:
        result = await services.review_withdrawal(
            withdrawal_id, action == "approve", db_user["tg_id"]
        )
    except services.ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await services.log_audit(
        db_user["tg_id"],
        "withdrawal_approved" if result["approved"] else "withdrawal_rejected",
        f"withdrawal={withdrawal_id} amount={result['amount']}",
    )
    await callback.answer("Tasdiqlandi" if result["approved"] else "Rad etildi")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    if result["approved"]:
        user_text = (
            "✅ <b>Pul yechish tasdiqlandi</b>\n\n"
            f"Summa: <b>{fmt_sum(result['amount'])}</b>\n"
            "Mablag' kartangizga o'tkazildi."
        )
    else:
        user_text = (
            "❌ <b>Pul yechish rad etildi</b>\n\n"
            f"Summa: {fmt_sum(result['amount'])}\n"
            "Mablag' balansingizga qaytarildi."
        )
    if result["user_tg"]:
        try:
            await callback.bot.send_message(result["user_tg"], user_text)
        except Exception:
            pass


@router.callback_query(F.data.startswith("admin:disp:creds:"))
async def cb_dispute_credentials(callback: CallbackQuery, db_user):
    if not _is_admin(db_user):
        await _deny(callback)
        return
    try:
        order_id = int(callback.data.split(":")[3])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return

    try:
        login, password = await services.get_credentials_admin(order_id)
    except services.ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.answer()
    await callback.message.answer(
        "🔑 <b>Shikoyat bo'yicha akkaunt ma'lumotlari</b>\n\n"
        f"Buyurtma: <b>#{order_id}</b>\n"
        f"Login: {code(login)}\n"
        f"Parol: {code(password)}\n\n"
        "⚠️ Bu ma'lumot faqat admin tekshiruvi uchun.",
        protect_content=True,
    )
    await services.log_audit(
        db_user["tg_id"], "dispute_credentials_view", f"order={order_id}"
    )


@router.callback_query(F.data.startswith("admin:disp:"))
async def cb_dispute_review(callback: CallbackQuery, db_user):
    if not _is_admin(db_user):
        await _deny(callback)
        return
    parts = callback.data.split(":")
    try:
        action = parts[2]
        order_id = int(parts[3])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ma'lumot", show_alert=True)
        return

    try:
        result = await services.resolve_dispute(order_id, action, db_user["tg_id"])
    except services.ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await services.log_audit(
        db_user["tg_id"], f"dispute_{action}", f"order={order_id}"
    )
    await callback.answer("Hal qilindi")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    if result["action"] == "refund":
        await callback.message.answer(
            f"↩️ Buyurtma #{order_id}: pul xaridorga qaytarildi "
            f"({fmt_sum(result['total'])})."
        )
        if result["buyer_tg"]:
            try:
                await callback.bot.send_message(
                    result["buyer_tg"],
                    "↩️ <b>Shikoyat hal qilindi</b>\n\n"
                    f"Buyurtma: <b>#{order_id}</b>\n"
                    f"Balansingizga qaytarildi: <b>{fmt_sum(result['total'])}</b>",
                )
            except Exception:
                pass
        if result["seller_tg"]:
            try:
                await callback.bot.send_message(
                    result["seller_tg"],
                    "⚠️ <b>Shikoyat natijasi</b>\n\n"
                    f"Buyurtma: <b>#{order_id}</b>\n"
                    "Mablag' xaridorga qaytarildi. Akkaunt yana sotuvga qaytarildi.",
                )
            except Exception:
                pass
    else:
        await callback.message.answer(
            f"💰 Buyurtma #{order_id}: mablag' sotuvchiga o'tkazildi."
        )
        if result["seller_tg"]:
            try:
                await callback.bot.send_message(
                    result["seller_tg"],
                    "✅ <b>Shikoyat hal qilindi</b>\n\n"
                    f"Buyurtma: <b>#{order_id}</b>\n"
                    f"Balansingizga qo'shildi: <b>{fmt_sum(result['seller_share'])}</b>",
                )
            except Exception:
                pass
        if result["buyer_tg"]:
            try:
                await callback.bot.send_message(
                    result["buyer_tg"],
                    "⚠️ <b>Shikoyat natijasi</b>\n\n"
                    f"Buyurtma: <b>#{order_id}</b>\n"
                    "Admin savdoni sotuvchi foydasiga hal qildi.",
                )
            except Exception:
                pass
