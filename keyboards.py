from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from config import SUPPORT_USERNAME
from utils import ACCOUNT_STATUS_LABELS, fmt_sum


def _btn(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def main_menu(is_seller: bool, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if is_seller:
        rows.append([_btn("🛍 Sotuvchi paneli", "seller:menu")])
    else:
        rows.append([_btn("🧑‍💼 Sotuvchi bo'lish", "seller:register")])
    rows.append([_btn("🔎 Akkaunt sotib olish", "buyer:search")])
    rows.append(
        [
            _btn("👛 Hamyonim", "wallet:menu"),
            _btn("📜 Mening xaridlarim", "buyer:history"),
        ]
    )
    rows.append(
        [
            _btn("👤 Profilim", "nav:profile"),
            _btn("📖 Qoidalar", "nav:rules"),
        ]
    )
    if SUPPORT_USERNAME:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🆘 Yordam", url=f"https://t.me/{SUPPORT_USERNAME}"
                )
            ]
        )
    if is_admin:
        rows.append([_btn("🛠 Admin panel", "admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn("🏠 Asosiy menyu", "nav:menu")]]
    )


def cancel_kb(target: str = "nav:menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("❌ Bekor qilish", target)]])


def phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def seller_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("➕ Akkaunt qo'shish", "seller:add")],
            [_btn("📦 Akkauntlarim", "seller:accounts")],
            [
                _btn("💰 Savdolarim", "seller:sales"),
                _btn("💳 Pul yechish", "seller:withdraw"),
            ],
            [
                _btn("👛 Balans", "wallet:menu"),
                _btn("👤 Profilim", "nav:profile"),
            ],
            [_btn("🏠 Asosiy menyu", "nav:menu")],
        ]
    )


def wallet_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("➕ Balans to'ldirish", "wallet:topup")],
            [_btn("🧾 Tranzaksiyalar", "wallet:history")],
            [_btn("📋 So'rovlarim", "wallet:requests")],
            [_btn("🏠 Asosiy menyu", "nav:menu")],
        ]
    )


def account_confirm(price: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("✅ Ha, akkaunt ishlaydi", "seller:addconfirm:yes")],
            [_btn("❌ Bekor qilish", "nav:menu")],
        ]
    )


def seller_accounts_kb(accounts) -> InlineKeyboardMarkup:
    rows = []
    for account in accounts:
        status = ACCOUNT_STATUS_LABELS.get(account["status"], "⚪").split()[0]
        rows.append(
            [
                _btn(
                    f"{status} #{account['id']} — {fmt_sum(account['price'])}",
                    f"seller:acct:{account['id']}",
                )
            ]
        )
    rows.append([_btn("🏠 Asosiy menyu", "nav:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def seller_account_detail(account) -> InlineKeyboardMarkup:
    account_id = account["id"]
    status = account["status"]
    rows = []
    if status in ("active", "paused"):
        rows.append([_btn("✏️ Narxni o'zgartirish", f"seller:editprice:{account_id}")])
        if status == "active":
            rows.append([_btn("⏸ Sotuvni to'xtatish", f"seller:pause:{account_id}")])
        else:
            rows.append([_btn("▶️ Sotuvni davom ettirish", f"seller:resume:{account_id}")])
        rows.append([_btn("🗑 O'chirish", f"seller:del:{account_id}")])
    rows.append([_btn("⬅️ Akkauntlarim", "seller:accounts")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buyer_accounts_kb(accounts) -> InlineKeyboardMarkup:
    rows = []
    for account in accounts:
        rows.append(
            [
                _btn(
                    f"#{account['id']} — {fmt_sum(account['price'])}",
                    f"shop:acct:{account['id']}",
                )
            ]
        )
    rows.append([_btn("🏠 Asosiy menyu", "nav:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def account_offer_kb(account_id: int, balance: int, price: int) -> InlineKeyboardMarkup:
    rows = []
    if balance >= price:
        rows.append([_btn("✅ Sotib olish", f"shop:confirm:{account_id}")])
    else:
        rows.append([_btn("➕ Balansni to'ldirish", "wallet:topup")])
    rows.append([_btn("🔙 Orqaga", "buyer:search")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_actions(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("✅ Akkauntni tasdiqlash", f"order:confirm:{order_id}")],
            [_btn("⚠️ Shikoyat qilish", f"order:dispute:{order_id}")],
            [_btn("🔑 Login-parolni ko'rsatish", f"order:creds:{order_id}")],
        ]
    )


def order_detail_kb(order_id: int, status: str) -> InlineKeyboardMarkup:
    rows = []
    if status == "checking":
        rows.append([_btn("✅ Tasdiqlash", f"order:confirm:{order_id}")])
        rows.append([_btn("⚠️ Shikoyat", f"order:dispute:{order_id}")])
    if status in ("checking", "completed", "auto_completed", "disputed"):
        rows.append([_btn("🔑 Login-parol", f"order:creds:{order_id}")])
    rows.append([_btn("🏠 Asosiy menyu", "nav:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("📊 Statistika", "admin:stats")],
            [
                _btn("💳 To'lovlar", "admin:topups"),
                _btn("💸 Pul yechish", "admin:withdrawals"),
            ],
            [_btn("⚠️ Shikoyatlar", "admin:disputes")],
            [
                _btn("🧾 Savdolar", "admin:sales"),
                _btn("👤 Foydalanuvchi", "admin:users"),
            ],
            [
                _btn("📝 Audit jurnal", "admin:audit"),
                _btn("📢 Xabar yuborish", "admin:broadcast"),
            ],
            [_btn("🏠 Asosiy menyu", "nav:menu")],
        ]
    )


def topup_review_kb(topup_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("✅ Tasdiqlash", f"admin:topup:approve:{topup_id}"),
                _btn("❌ Rad etish", f"admin:topup:reject:{topup_id}"),
            ]
        ]
    )


def withdrawal_review_kb(withdrawal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("✅ To'landi", f"admin:wd:approve:{withdrawal_id}"),
                _btn("❌ Rad etish", f"admin:wd:reject:{withdrawal_id}"),
            ]
        ]
    )


def dispute_review_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🔑 Login-parolni tekshirish", f"admin:disp:creds:{order_id}")],
            [
                _btn("↩️ Pulni qaytarish", f"admin:disp:refund:{order_id}"),
                _btn("💰 Sotuvchiga berish", f"admin:disp:release:{order_id}"),
            ]
        ]
    )


def broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("📤 Yuborish", "admin:bcast:go")],
            [_btn("❌ Bekor qilish", "admin:menu")],
        ]
    )


def admin_users_kb(user) -> InlineKeyboardMarkup:
    if user["is_blocked"]:
        action = _btn("🔓 Blokdan chiqarish", f"admin:unblock:{user['id']}")
    else:
        action = _btn("🚫 Bloklash", f"admin:block:{user['id']}")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [action],
            [_btn("💰 Balansni o'zgartirish", f"admin:bal:{user['id']}")],
            [_btn("⬅️ Admin panel", "admin:menu")],
        ]
    )
