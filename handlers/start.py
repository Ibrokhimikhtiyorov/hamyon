from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

import services
from config import (
    AUTO_COMPLETE_MINUTES,
    CHECK_MINUTES,
    COMMISSION_PERCENT,
    PENALTY_INTERVAL_MINUTES,
    PENALTY_PERCENT,
    SUPPORT_USERNAME,
)
from keyboards import back_to_menu, main_menu
from utils import GAME_ACCOUNT_EXAMPLES, code, esc, fmt_dt, fmt_sum

router = Router(name="start")

WARNING = (
    "⚠️ <b>Diqqat!</b>\n\n"
    "Ushbu platformada amalga oshirilgan har bir muvaffaqiyatli savdodan "
    f"<b>{COMMISSION_PERCENT}% komissiya</b> ushlab qolinadi. "
    "Botdan foydalanishdan oldin qoidalar bilan tanishib chiqing.\n\n"
    f"🎮 Bu yerda {GAME_ACCOUNT_EXAMPLES}ni sotish mumkin."
)


def menu_for(db_user):
    return main_menu(bool(db_user["is_seller"]), bool(db_user["is_admin"]))


async def show_main_menu(message: Message, db_user, text: str = "🏠 <b>Asosiy menyu</b>\n\nKerakli bo'limni tanlang."):
    await message.answer(text, reply_markup=menu_for(db_user))


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db_user):
    await state.clear()
    await message.answer(WARNING, reply_markup=ReplyKeyboardRemove())
    await show_main_menu(message, db_user)


@router.message(Command("help"))
async def cmd_help(message: Message, db_user):
    text = (
        "ℹ️ <b>Yordam</b>\n\n"
        "/start — asosiy menyu\n"
        "/cancel — joriy amalni bekor qilish\n"
        "/help — yordam\n\n"
    )
    if SUPPORT_USERNAME:
        text += (
            "Savol va muammolar uchun qo'llab-quvvatlash xizmatiga yozing: "
            f"@{SUPPORT_USERNAME}"
        )
    else:
        text += "Savol va muammolar uchun admin bilan bog'laning."
    await message.answer(text, reply_markup=menu_for(db_user))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, db_user):
    await state.clear()
    await message.answer("❌ Amal bekor qilindi.", reply_markup=ReplyKeyboardRemove())
    await show_main_menu(message, db_user)


@router.callback_query(F.data == "nav:menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    await callback.answer()
    await show_main_menu(callback.message, db_user)


@router.callback_query(F.data == "flow:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    await callback.answer("Bekor qilindi")
    await callback.message.answer("❌ Amal bekor qilindi.", reply_markup=ReplyKeyboardRemove())
    await show_main_menu(callback.message, db_user)


@router.callback_query(F.data == "nav:rules")
async def cb_rules(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    await callback.answer()
    text = (
        "📖 <b>Platforma qoidalari</b>\n\n"
        f"🎮 Platformada {GAME_ACCOUNT_EXAMPLES}ni sotish mumkin.\n\n"
        f"1. Har bir muvaffaqiyatli savdodan bot <b>{COMMISSION_PERCENT}%</b> komissiya oladi, "
        f"sotuvchiga <b>{100 - COMMISSION_PERCENT}%</b> o'tkaziladi.\n\n"
        f"2. Xaridor to'lovni amalga oshirgach, akkaunt login-paroli ochiladi va "
        f"tekshirish uchun <b>{CHECK_MINUTES} daqiqa</b> vaqt beriladi.\n\n"
        f"3. {CHECK_MINUTES} daqiqadan oshgach, har bir {PENALTY_INTERVAL_MINUTES} daqiqa "
        f"kechikish uchun akkaunt narxining <b>{PENALTY_PERCENT}%</b> miqdorida jarima "
        "avtomatik hisoblanadi.\n\n"
        f"4. Tekshirish muddati tugagach yana {AUTO_COMPLETE_MINUTES} daqiqa ichida "
        "tasdiqlanmasa, buyurtma avtomatik yakunlanadi.\n\n"
        "5. Login va parollar shifrlangan holda saqlanadi va faqat xaridorga ko'rsatiladi.\n\n"
        "6. Nizoli holatlar admin tomonidan ko'rib chiqiladi. Firibgarlik aniqlansa, "
        "hisob bloklanadi.\n\n"
        "7. Platforma akkauntlarning qonuniyligi uchun javobgar emas. Faqat o'zingizga "
        "tegishli akkauntlarni soting."
    )
    await callback.message.answer(text, reply_markup=back_to_menu())


@router.callback_query(F.data == "nav:profile")
async def cb_profile(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    await callback.answer()
    user = await services.get_user(db_user["id"])
    if user is None:
        await callback.message.answer("Foydalanuvchi topilmadi.", reply_markup=back_to_menu())
        return

    role = "Sotuvchi" if user["is_seller"] else "Xaridor"
    if user["is_admin"]:
        role += " + Admin"

    text = (
        "👤 <b>Profil</b>\n\n"
        f"🆔 Telegram ID: {code(user['tg_id'])}\n"
        f"📛 Ism: {esc(user['full_name'])}\n"
        f"🔗 Username: @{esc(user['username'] or 'yo`q')}\n"
        f"📱 Telefon: {esc(user['phone'] or 'tasdiqlanmagan')}\n"
        f"🎭 Rol: {role}\n"
        f"💰 Balans: <b>{fmt_sum(user['balance'])}</b>\n"
    )
    if user["is_seller"]:
        text += f"🔑 Sotuvchi kodi: {code(user['seller_code'])}\n"
        stats = await services.seller_stats(user["id"])
        text += f"📦 Sotuvdagi akkauntlar: {stats['active']}\n"
        text += f"⏸ To'xtatilgan akkauntlar: {stats['paused']}\n"
        text += f"✅ Sotilgan akkauntlar: {stats['sold']}\n"
    text += f"📅 Ro'yxatdan o'tgan: {fmt_dt(user['created_at'])}"

    await callback.message.answer(text, reply_markup=back_to_menu())
