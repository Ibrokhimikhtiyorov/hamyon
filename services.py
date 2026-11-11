import logging
import math
from datetime import timedelta

import aiosqlite

import crypto
from config import (
    ADMIN_IDS,
    AUTO_COMPLETE_MINUTES,
    CHECK_MINUTES,
    COMMISSION_PERCENT,
    MIN_PRICE,
    PAYMENT_MINUTES,
    PENALTY_INTERVAL_MINUTES,
    PENALTY_PERCENT,
    SELLER_SHARE_PERCENT,
)
from db import db
from utils import (
    deadline_after,
    fmt_dt,
    fmt_sum,
    generate_code,
    parse_dt,
    utcnow,
    utcnow_iso,
)

logger = logging.getLogger(__name__)


class ServiceError(Exception):
    """Foydalanuvchiga ko'rsatiladigan biznes-xatolik."""


class InsufficientFunds(ServiceError):
    def __init__(self, required: int, available: int):
        self.required = required
        self.available = available
        super().__init__(
            f"Balans yetarli emas. Kerak: {fmt_sum(required)}, mavjud: {fmt_sum(available)}"
        )


async def _fetchone(conn: aiosqlite.Connection, sql: str, params: tuple = ()):
    cursor = await conn.execute(sql, params)
    row = await cursor.fetchone()
    await cursor.close()
    return row


async def _fetchall(conn: aiosqlite.Connection, sql: str, params: tuple = ()):
    cursor = await conn.execute(sql, params)
    rows = await cursor.fetchall()
    await cursor.close()
    return list(rows)


async def ensure_user(tg_id: int, full_name: str, username: str | None):
    is_admin = 1 if tg_id in ADMIN_IDS else 0
    row = await db.fetchone("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    if row is None:
        await db.execute(
            "INSERT INTO users (tg_id, full_name, username, is_admin, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (tg_id, full_name or "", username, is_admin, utcnow_iso()),
        )
    else:
        await db.execute(
            "UPDATE users SET full_name = ?, username = ?, is_admin = ? WHERE id = ?",
            (full_name or "", username, is_admin, row["id"]),
        )
    return await db.fetchone("SELECT * FROM users WHERE tg_id = ?", (tg_id,))


async def get_user_by_tg(tg_id: int):
    return await db.fetchone("SELECT * FROM users WHERE tg_id = ?", (tg_id,))


async def get_user(user_id: int):
    return await db.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))


async def get_user_by_code(seller_code: str):
    return await db.fetchone(
        "SELECT * FROM users WHERE seller_code = ? AND is_seller = 1 AND is_blocked = 0",
        (seller_code.strip().upper(),),
    )


async def register_seller(user_id: int, phone: str):
    user = await get_user(user_id)
    if user is None:
        raise ServiceError("Foydalanuvchi topilmadi")
    if user["is_seller"]:
        return user

    while True:
        seller_code = generate_code()
        exists = await db.fetchone(
            "SELECT 1 FROM users WHERE seller_code = ?", (seller_code,)
        )
        if not exists:
            break

    await db.execute(
        "UPDATE users SET phone = ?, seller_code = ?, is_seller = 1 WHERE id = ?",
        (phone, seller_code, user_id),
    )
    return await get_user(user_id)


async def seller_stats(seller_id: int) -> dict:
    async def count(status: str) -> int:
        row = await db.fetchone(
            "SELECT COUNT(*) AS c FROM accounts WHERE seller_id = ? AND status = ?",
            (seller_id, status),
        )
        return int(row["c"]) if row else 0

    return {
        "active": await count("active"),
        "paused": await count("paused"),
        "sold": await count("sold"),
    }


async def create_account(seller_id: int, login: str, password: str, price: int) -> int:
    login = login.strip()
    if not 2 <= len(login) <= 200:
        raise ServiceError("Login 2 dan 200 belgigacha bo'lishi kerak")
    if not 4 <= len(password) <= 200:
        raise ServiceError("Parol 4 dan 200 belgigacha bo'lishi kerak")
    if price < MIN_PRICE:
        raise ServiceError(f"Narx kamida {fmt_sum(MIN_PRICE)} bo'lishi kerak")

    hashed = crypto.login_hash(login)
    exists = await db.fetchone(
        "SELECT 1 FROM accounts WHERE login_hash = ?", (hashed,)
    )
    if exists:
        raise ServiceError("Bu login bilan akkaunt allaqachon qo'shilgan")

    account_id = await db.execute(
        "INSERT INTO accounts "
        "(seller_id, login_enc, login_hash, password_enc, price, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'active', ?)",
        (
            seller_id,
            crypto.encrypt(login),
            hashed,
            crypto.encrypt(password),
            price,
            utcnow_iso(),
        ),
    )
    return account_id


async def list_seller_accounts(seller_id: int, include_sold: bool = True):
    if include_sold:
        return await db.fetchall(
            "SELECT * FROM accounts WHERE seller_id = ? "
            "AND status IN ('active','paused','sold','refunded') "
            "ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 "
            "WHEN 'sold' THEN 2 ELSE 3 END, id DESC",
            (seller_id,),
        )
    return await db.fetchall(
        "SELECT * FROM accounts WHERE seller_id = ? AND status = 'active' ORDER BY id DESC",
        (seller_id,),
    )


async def get_account(account_id: int):
    return await db.fetchone("SELECT * FROM accounts WHERE id = ?", (account_id,))


async def get_account_login(account_id: int) -> str:
    account = await get_account(account_id)
    if account is None:
        raise ServiceError("Akkaunt topilmadi")
    return crypto.decrypt(account["login_enc"])


async def delete_account(account_id: int, seller_id: int) -> None:
    account = await get_account(account_id)
    if account is None:
        raise ServiceError("Akkaunt topilmadi")
    if account["seller_id"] != seller_id:
        raise ServiceError("Bu akkaunt sizga tegishli emas")
    if account["status"] not in ("active", "paused"):
        raise ServiceError("Faqat sotuvdagi yoki to'xtatilgan akkauntni o'chirish mumkin")

    active_order = await db.fetchone(
        "SELECT 1 FROM orders WHERE account_id = ? AND status IN ('checking','disputed')",
        (account_id,),
    )
    if active_order:
        raise ServiceError("Bu akkaunt bo'yicha faol buyurtma mavjud")

    await db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


async def set_account_paused(account_id: int, seller_id: int, paused: bool) -> None:
    account = await get_account(account_id)
    if account is None or account["seller_id"] != seller_id:
        raise ServiceError("Akkaunt topilmadi")
    if account["status"] not in ("active", "paused"):
        raise ServiceError("Bu akkaunt holatini o'zgartirib bo'lmaydi")

    active_order = await db.fetchone(
        "SELECT 1 FROM orders WHERE account_id = ? AND status IN ('checking','disputed')",
        (account_id,),
    )
    if active_order:
        raise ServiceError("Faol buyurtma mavjud, avval uni yakunlang")

    await db.execute(
        "UPDATE accounts SET status = ? WHERE id = ?",
        ("paused" if paused else "active", account_id),
    )


async def update_account_price(account_id: int, seller_id: int, price: int) -> None:
    if price < MIN_PRICE:
        raise ServiceError(f"Narx kamida {fmt_sum(MIN_PRICE)} bo'lishi kerak")
    account = await get_account(account_id)
    if account is None or account["seller_id"] != seller_id:
        raise ServiceError("Akkaunt topilmadi")
    if account["status"] not in ("active", "paused"):
        raise ServiceError("Sotilgan akkaunt narxini o'zgartirib bo'lmaydi")
    await db.execute(
        "UPDATE accounts SET price = ? WHERE id = ?", (price, account_id)
    )


async def list_active_accounts(seller_id: int):
    return await db.fetchall(
        "SELECT a.* FROM accounts a JOIN users u ON u.id = a.seller_id "
        "WHERE a.seller_id = ? AND a.status = 'active' AND u.is_blocked = 0 "
        "ORDER BY a.id DESC",
        (seller_id,),
    )


async def create_order(buyer_id: int, account_id: int):
    now = utcnow()
    check_deadline = (now + timedelta(minutes=CHECK_MINUTES)).isoformat(timespec="seconds")
    auto_complete_at = (
        now + timedelta(minutes=CHECK_MINUTES + AUTO_COMPLETE_MINUTES)
    ).isoformat(timespec="seconds")

    async with db.transaction() as conn:
        account = await _fetchone(
            conn, "SELECT * FROM accounts WHERE id = ?", (account_id,)
        )
        if account is None:
            raise ServiceError("Akkaunt topilmadi")
        if account["status"] != "active":
            raise ServiceError("Bu akkaunt allaqachon sotilgan")
        if account["seller_id"] == buyer_id:
            raise ServiceError("O'z akkauntingizni sotib ola olmaysiz")

        seller = await _fetchone(
            conn, "SELECT * FROM users WHERE id = ?", (account["seller_id"],)
        )
        if seller is None or seller["is_blocked"]:
            raise ServiceError("Sotuvchi faol emas")

        buyer = await _fetchone(conn, "SELECT * FROM users WHERE id = ?", (buyer_id,))
        if buyer is None:
            raise ServiceError("Foydalanuvchi topilmadi")
        if buyer["is_blocked"]:
            raise ServiceError("Hisobingiz bloklangan")
        if buyer["balance"] < account["price"]:
            raise InsufficientFunds(account["price"], buyer["balance"])

        claimed = await conn.execute(
            "UPDATE accounts SET status = 'sold', sold_at = ? "
            "WHERE id = ? AND status = 'active'",
            (utcnow_iso(), account_id),
        )
        if claimed.rowcount == 0:
            await claimed.close()
            raise ServiceError("Bu akkaunt allaqachon sotilgan")
        await claimed.close()

        debited = await conn.execute(
            "UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ?",
            (account["price"], buyer_id, account["price"]),
        )
        if debited.rowcount == 0:
            await debited.close()
            raise InsufficientFunds(account["price"], buyer["balance"])
        await debited.close()

        cursor = await conn.execute(
            "INSERT INTO orders "
            "(account_id, seller_id, buyer_id, price, status, check_deadline, "
            "auto_complete_at, created_at) VALUES (?, ?, ?, ?, 'checking', ?, ?, ?)",
            (
                account_id,
                account["seller_id"],
                buyer_id,
                account["price"],
                check_deadline,
                auto_complete_at,
                utcnow_iso(),
            ),
        )
        order_id = int(cursor.lastrowid or 0)
        await cursor.close()

        await conn.execute(
            "INSERT INTO transactions (user_id, order_id, amount, type, description, created_at) "
            "VALUES (?, ?, ?, 'purchase', ?, ?)",
            (
                buyer_id,
                order_id,
                -account["price"],
                f"Akkaunt #{account_id} xaridi uchun escrow",
                utcnow_iso(),
            ),
        )

        order = await _fetchone(conn, "SELECT * FROM orders WHERE id = ?", (order_id,))

    return order, account, seller


async def get_order(order_id: int):
    return await db.fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))


async def get_order_with_people(order_id: int):
    return await db.fetchone(
        "SELECT o.*, b.tg_id AS buyer_tg, b.full_name AS buyer_name, "
        "s.tg_id AS seller_tg, s.full_name AS seller_name "
        "FROM orders o "
        "JOIN users b ON b.id = o.buyer_id "
        "JOIN users s ON s.id = o.seller_id "
        "WHERE o.id = ?",
        (order_id,),
    )


async def get_credentials(order_id: int, buyer_id: int):
    order = await get_order(order_id)
    if order is None:
        raise ServiceError("Buyurtma topilmadi")
    if order["buyer_id"] != buyer_id:
        raise ServiceError("Bu buyurtma sizga tegishli emas")
    if order["status"] not in ("checking", "completed", "auto_completed", "disputed"):
        raise ServiceError("Bu buyurtma bo'yicha login-parol ko'rsatilmaydi")

    account = await get_account(order["account_id"])
    if account is None:
        raise ServiceError("Akkaunt topilmadi")
    try:
        login = crypto.decrypt(account["login_enc"])
        password = crypto.decrypt(account["password_enc"])
    except ValueError as exc:
        raise ServiceError("Login-parolni ochishda xatolik") from exc
    return login, password


async def get_credentials_admin(order_id: int):
    order = await get_order(order_id)
    if order is None:
        raise ServiceError("Buyurtma topilmadi")
    account = await get_account(order["account_id"])
    if account is None:
        raise ServiceError("Akkaunt topilmadi")
    try:
        return (
            crypto.decrypt(account["login_enc"]),
            crypto.decrypt(account["password_enc"]),
        )
    except ValueError as exc:
        raise ServiceError("Login-parolni ochishda xatolik") from exc


async def log_audit(actor_tg: int | None, action: str, details: str = "") -> None:
    await db.execute(
        "INSERT INTO audit_logs (actor_tg, action, details, created_at) "
        "VALUES (?, ?, ?, ?)",
        (actor_tg, action, details[:1000], utcnow_iso()),
    )


async def list_audit_logs(limit: int = 20):
    return await db.fetchall(
        "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)
    )


async def adjust_balance(user_id: int, amount: int, actor_tg: int, note: str = ""):
    async with db.transaction() as conn:
        user = await _fetchone(conn, "SELECT * FROM users WHERE id = ?", (user_id,))
        if user is None:
            raise ServiceError("Foydalanuvchi topilmadi")
        await conn.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ?", (amount, user_id)
        )
        await conn.execute(
            "INSERT INTO transactions (user_id, amount, type, description, created_at) "
            "VALUES (?, ?, 'adjustment', ?, ?)",
            (
                user_id,
                amount,
                note or f"Admin tomonidan balans o'zgartirildi ({actor_tg})",
                utcnow_iso(),
            ),
        )
        await conn.execute(
            "INSERT INTO audit_logs (actor_tg, action, details, created_at) "
            "VALUES (?, 'balance_adjust', ?, ?)",
            (actor_tg, f"user={user_id} amount={amount} {note}", utcnow_iso()),
        )
        updated = await _fetchone(
            conn, "SELECT balance FROM users WHERE id = ?", (user_id,)
        )
        return int(updated["balance"]) if updated else int(user["balance"]) + amount


async def _settle_order(conn: aiosqlite.Connection, order, new_status: str):
    total = int(order["price"]) + int(order["penalty"])
    seller_share = total * SELLER_SHARE_PERCENT // 100
    commission = total - seller_share
    now = utcnow_iso()

    await conn.execute(
        "UPDATE users SET balance = balance + ? WHERE id = ?",
        (seller_share, order["seller_id"]),
    )
    await conn.execute(
        "UPDATE orders SET status = ?, completed_at = ? WHERE id = ?",
        (new_status, now, order["id"]),
    )
    await conn.execute(
        "UPDATE accounts SET status = 'sold', sold_at = COALESCE(sold_at, ?) WHERE id = ?",
        (now, order["account_id"]),
    )
    await conn.execute(
        "INSERT INTO transactions (user_id, order_id, amount, type, description, created_at) "
        "VALUES (?, ?, ?, 'sale', ?, ?)",
        (
            order["seller_id"],
            order["id"],
            seller_share,
            f"Savdo #{order['id']} daromadi ({SELLER_SHARE_PERCENT}%)",
            now,
        ),
    )
    await conn.execute(
        "INSERT INTO transactions (user_id, order_id, amount, type, description, created_at) "
        "VALUES (NULL, ?, ?, 'commission', ?, ?)",
        (
            order["id"],
            commission,
            f"Savdo #{order['id']} komissiyasi ({COMMISSION_PERCENT}%)",
            now,
        ),
    )

    buyer = await _fetchone(conn, "SELECT tg_id FROM users WHERE id = ?", (order["buyer_id"],))
    seller = await _fetchone(conn, "SELECT tg_id FROM users WHERE id = ?", (order["seller_id"],))
    return {
        "order_id": order["id"],
        "status": new_status,
        "price": int(order["price"]),
        "penalty": int(order["penalty"]),
        "total": total,
        "seller_share": seller_share,
        "commission": commission,
        "buyer_tg": buyer["tg_id"] if buyer else None,
        "seller_tg": seller["tg_id"] if seller else None,
    }


async def confirm_order(order_id: int, buyer_id: int):
    async with db.transaction() as conn:
        order = await _fetchone(conn, "SELECT * FROM orders WHERE id = ?", (order_id,))
        if order is None:
            raise ServiceError("Buyurtma topilmadi")
        if order["buyer_id"] != buyer_id:
            raise ServiceError("Bu buyurtma sizga tegishli emas")
        if order["status"] == "disputed":
            raise ServiceError("Shikoyat ko'rib chiqilmoqda. Admin javobini kuting")
        if order["status"] != "checking":
            raise ServiceError("Bu buyurtma allaqachon yakunlangan")
        return await _settle_order(conn, order, "completed")


async def create_dispute(order_id: int, buyer_id: int, reason: str):
    reason = reason.strip()
    if not 5 <= len(reason) <= 1000:
        raise ServiceError("Shikoyat sababi 5 dan 1000 belgigacha bo'lishi kerak")

    async with db.transaction() as conn:
        order = await _fetchone(conn, "SELECT * FROM orders WHERE id = ?", (order_id,))
        if order is None:
            raise ServiceError("Buyurtma topilmadi")
        if order["buyer_id"] != buyer_id:
            raise ServiceError("Bu buyurtma sizga tegishli emas")
        if order["status"] != "checking":
            raise ServiceError("Bu buyurtma bo'yicha shikoyat ochib bo'lmaydi")

        cursor = await conn.execute(
            "INSERT INTO disputes (order_id, buyer_id, reason, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (order_id, buyer_id, reason, utcnow_iso()),
        )
        await cursor.close()
        await conn.execute(
            "UPDATE orders SET status = 'disputed' WHERE id = ?", (order_id,)
        )
        return await _fetchone(conn, "SELECT * FROM orders WHERE id = ?", (order_id,))


async def accrue_penalties():
    events: list[tuple[int, str, int | None]] = []
    rows = await db.fetchall("SELECT id FROM orders WHERE status = 'checking'")
    interval_seconds = PENALTY_INTERVAL_MINUTES * 60

    for row in rows:
        async with db.transaction() as conn:
            order = await _fetchone(
                conn, "SELECT * FROM orders WHERE id = ?", (row["id"],)
            )
            if order is None or order["status"] != "checking":
                continue

            now = utcnow()
            deadline = parse_dt(order["check_deadline"])
            auto_at = parse_dt(order["auto_complete_at"])
            if deadline is None or auto_at is None:
                continue

            if now < deadline:
                remaining = (deadline - now).total_seconds()
                buyer = await _fetchone(
                    conn, "SELECT tg_id FROM users WHERE id = ?", (order["buyer_id"],)
                )
                if (
                    buyer is not None
                    and remaining <= 60
                    and not order["reminder_1_sent"]
                ):
                    await conn.execute(
                        "UPDATE orders SET reminder_1_sent = 1, reminder_5_sent = 1 "
                        "WHERE id = ?",
                        (order["id"],),
                    )
                    events.append(
                        (
                            buyer["tg_id"],
                            ("⏳ <b>Diqqat, 1 daqiqa qoldi!</b>\n\n"
                            f"Buyurtma: <b>#{order['id']}</b>\n"
                            "Akkauntni tekshirib, «Tasdiqlash» tugmasini bosing. "
                            "Muddat tugasa jarima hisoblana boshlaydi."),
                            order["id"],
                        )
                    )
                elif (
                    buyer is not None
                    and remaining <= 300
                    and not order["reminder_5_sent"]
                ):
                    await conn.execute(
                        "UPDATE orders SET reminder_5_sent = 1 WHERE id = ?",
                        (order["id"],),
                    )
                    events.append(
                        (
                            buyer["tg_id"],
                            ("⏳ <b>Tekshirish uchun 5 daqiqa qoldi</b>\n\n"
                            f"Buyurtma: <b>#{order['id']}</b>\n"
                            "Iltimos, akkauntni tekshirib, tasdiqlang yoki shikoyat qiling."),
                            order["id"],
                        )
                    )

            if now > deadline:
                delay = (now - deadline).total_seconds()
                ticks_due = max(1, math.ceil(delay / interval_seconds))
                new_ticks = ticks_due - int(order["penalty_ticks"])
                if new_ticks > 0:
                    penalty_each = max(
                        1, round(int(order["price"]) * PENALTY_PERCENT / 100)
                    )
                    extra = penalty_each * new_ticks
                    # Xavfsizlik: jarima faqat xaridorning real balansidagi
                    # mablag' hisobidan yechiladi — balans minusga tushmaydi
                    # va platforma yig'ilmagan jarimani to'lamaydi.
                    buyer_row = await _fetchone(
                        conn,
                        "SELECT balance FROM users WHERE id = ?",
                        (order["buyer_id"],),
                    )
                    available = (
                        max(int(buyer_row["balance"]), 0)
                        if buyer_row is not None
                        else 0
                    )
                    if extra > available:
                        extra = available
                    if extra > 0:
                        await conn.execute(
                            "UPDATE users SET balance = balance - ? "
                            "WHERE id = ? AND balance >= ?",
                            (extra, order["buyer_id"], extra),
                        )
                        await conn.execute(
                            "UPDATE orders SET penalty = penalty + ?, penalty_ticks = ?, "
                            "last_penalty_at = ? WHERE id = ?",
                            (extra, ticks_due, utcnow_iso(), order["id"]),
                        )
                        await conn.execute(
                            "INSERT INTO transactions "
                            "(user_id, order_id, amount, type, description, created_at) "
                            "VALUES (?, ?, ?, 'penalty', ?, ?)",
                            (
                                order["buyer_id"],
                                order["id"],
                                -extra,
                                (f"Buyurtma #{order['id']} kechikish jarimasi "
                                f"({PENALTY_PERCENT}% x {new_ticks})"),
                                utcnow_iso(),
                            ),
                        )

                        buyer = await _fetchone(
                            conn,
                            "SELECT tg_id FROM users WHERE id = ?",
                            (order["buyer_id"],),
                        )
                        order = await _fetchone(
                            conn, "SELECT * FROM orders WHERE id = ?", (order["id"],)
                        )
                        if buyer:
                            total = int(order["price"]) + int(order["penalty"])
                            events.append(
                                (
                                    buyer["tg_id"],
                                    ("⏰ <b>Tekshirish muddati tugadi!</b>\n\n"
                                    f"Buyurtma: <b>#{order['id']}</b>\n"
                                    f"Har bir {PENALTY_INTERVAL_MINUTES} daqiqa kechikish uchun "
                                    f"narxning {PENALTY_PERCENT}% qo'shimcha haq hisoblanadi.\n\n"
                                    f"Qo'shilgan jarima: <b>{fmt_sum(extra)}</b>\n"
                                    f"Joriy jarima: <b>{fmt_sum(order['penalty'])}</b>\n"
                                    f"Umumiy summa: <b>{fmt_sum(total)}</b>\n\n"
                                    "Akkauntni tekshirib bo'lsangiz, «Tasdiqlash» tugmasini bosing."),
                                    order["id"],
                                )
                            )
                    else:
                        # Balansda mablag' yo'q — jarima to'planmaydi,
                        # faqat hisoblangan ticklar belgilanadi.
                        await conn.execute(
                            "UPDATE orders SET penalty_ticks = ? WHERE id = ?",
                            (ticks_due, order["id"]),
                        )

            if now >= auto_at:
                order = await _fetchone(
                    conn, "SELECT * FROM orders WHERE id = ?", (order["id"],)
                )
                if order is not None and order["status"] == "checking":
                    info = await _settle_order(conn, order, "auto_completed")
                    if info["buyer_tg"]:
                        events.append(
                            (
                                info["buyer_tg"],
                                ("⌛ <b>Buyurtma avtomatik yakunlandi</b>\n\n"
                                f"Buyurtma: <b>#{info['order_id']}</b>\n"
                                f"Tekshirish vaqti tugagani uchun savdo avtomatik "
                                f"yakunlandi.\nJarima: <b>{fmt_sum(info['penalty'])}</b>\n"
                                f"Jami: <b>{fmt_sum(info['total'])}</b>"),
                                None,
                            )
                        )
                    if info["seller_tg"]:
                        events.append(
                            (
                                info["seller_tg"],
                                ("💰 <b>Savdo avtomatik yakunlandi</b>\n\n"
                                f"Buyurtma: <b>#{info['order_id']}</b>\n"
                                f"Balansingizga qo'shildi: <b>{fmt_sum(info['seller_share'])}</b> "
                                f"({SELLER_SHARE_PERCENT}%)."),
                                None,
                            )
                        )
    return events


async def resolve_dispute(order_id: int, action: str, admin_tg_id: int):
    if action not in ("refund", "release"):
        raise ServiceError("Noto'g'ri amal")

    async with db.transaction() as conn:
        order = await _fetchone(conn, "SELECT * FROM orders WHERE id = ?", (order_id,))
        dispute = await _fetchone(
            conn, "SELECT * FROM disputes WHERE order_id = ?", (order_id,)
        )
        if order is None or dispute is None:
            raise ServiceError("Shikoyat topilmadi")
        if dispute["status"] != "pending":
            raise ServiceError("Bu shikoyat allaqachon hal qilingan")
        if order["status"] != "disputed":
            raise ServiceError("Buyurtma shikoyat holatida emas")

        now = utcnow_iso()
        if action == "refund":
            total = int(order["price"]) + int(order["penalty"])
            await conn.execute(
                "UPDATE users SET balance = balance + ? WHERE id = ?",
                (total, order["buyer_id"]),
            )
            await conn.execute(
                "UPDATE orders SET status = 'refunded', completed_at = ? WHERE id = ?",
                (now, order_id),
            )
            await conn.execute(
                "UPDATE accounts SET status = 'active', sold_at = NULL WHERE id = ?",
                (order["account_id"],),
            )
            await conn.execute(
                "INSERT INTO transactions "
                "(user_id, order_id, amount, type, description, created_at) "
                "VALUES (?, ?, ?, 'refund', ?, ?)",
                (
                    order["buyer_id"],
                    order_id,
                    total,
                    f"Buyurtma #{order_id} uchun pul qaytarildi",
                    now,
                ),
            )
            await conn.execute(
                "UPDATE disputes SET status = 'refunded', resolved_by = ?, resolved_at = ? "
                "WHERE order_id = ?",
                (admin_tg_id, now, order_id),
            )
            buyer = await _fetchone(
                conn, "SELECT tg_id FROM users WHERE id = ?", (order["buyer_id"],)
            )
            seller = await _fetchone(
                conn, "SELECT tg_id FROM users WHERE id = ?", (order["seller_id"],)
            )
            return {
                "action": "refund",
                "order_id": order_id,
                "total": total,
                "buyer_tg": buyer["tg_id"] if buyer else None,
                "seller_tg": seller["tg_id"] if seller else None,
            }

        order = await _fetchone(conn, "SELECT * FROM orders WHERE id = ?", (order_id,))
        info = await _settle_order(conn, order, "completed")
        await conn.execute(
            "UPDATE disputes SET status = 'released', resolved_by = ?, resolved_at = ? "
            "WHERE order_id = ?",
            (admin_tg_id, now, order_id),
        )
        info["action"] = "release"
        return info


async def list_buyer_orders(buyer_id: int, limit: int = 20):
    return await db.fetchall(
        "SELECT o.*, a.price AS account_price, s.full_name AS seller_name, "
        "s.seller_code AS seller_code "
        "FROM orders o "
        "JOIN accounts a ON a.id = o.account_id "
        "JOIN users s ON s.id = o.seller_id "
        "WHERE o.buyer_id = ? ORDER BY o.id DESC LIMIT ?",
        (buyer_id, limit),
    )


async def list_seller_sales(seller_id: int, limit: int = 20):
    return await db.fetchall(
        "SELECT o.*, b.full_name AS buyer_name, b.tg_id AS buyer_tg "
        "FROM orders o JOIN users b ON b.id = o.buyer_id "
        "WHERE o.seller_id = ? ORDER BY o.id DESC LIMIT ?",
        (seller_id, limit),
    )


async def list_transactions(user_id: int, limit: int = 15):
    return await db.fetchall(
        "SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    )


async def list_user_topups(user_id: int, limit: int = 10):
    manual = await db.fetchall(
        "SELECT id, amount, status, created_at, 'manual' AS source "
        "FROM topups WHERE user_id = ?",
        (user_id,),
    )
    automatic = await db.fetchall(
        "SELECT id, amount, status, created_at, 'hamyon' AS source "
        "FROM hamyon_payments WHERE user_id = ?",
        (user_id,),
    )
    items = [dict(row) for row in manual] + [dict(row) for row in automatic]
    items.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
    return items[:limit]


async def list_user_withdrawals(user_id: int, limit: int = 10):
    return await db.fetchall(
        "SELECT * FROM withdrawals WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    )


async def create_hamyon_payment(
    user_id: int, amount: int, order_id: str, purpose: str = "topup"
) -> str:
    """Yangi to'lov yaratadi va 5 daqiqalik muddatni belgilaydi.

    purpose: 'topup' yoki 'buy:<account_id>' (to'g'ridan-to'g'ri xarid).
    """
    now = utcnow_iso()
    expires_at = deadline_after(PAYMENT_MINUTES)
    await db.execute(
        "INSERT INTO hamyon_payments "
        "(order_id, user_id, amount, purpose, expires_at, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
        (order_id, user_id, amount, purpose, expires_at, now, now),
    )
    return expires_at


async def expire_stale_hamyon_payments() -> list[tuple[int, int]]:
    """Muddati o'tgan pending to'lovlarni bekor qiladi (buyer tg_id, amount)."""
    now = utcnow_iso()
    rows = await db.fetchall(
        "SELECT id, user_id, amount FROM hamyon_payments "
        "WHERE status = 'pending' AND expires_at IS NOT NULL AND expires_at <= ?",
        (now,),
    )
    events: list[tuple[int, int]] = []
    for row in rows:
        await db.execute(
            "UPDATE hamyon_payments SET status = 'expired', updated_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (now, row["id"]),
        )
        user = await get_user(row["user_id"])
        if user is not None:
            events.append((int(user["tg_id"]), int(row["amount"])))
    return events


async def update_hamyon_payment_created(
    order_id: str, payment_id: str, card: str | None, expires_in: int | None
):
    await db.execute(
        "UPDATE hamyon_payments SET payment_id = ?, card = ?, expires_in = ?, "
        "updated_at = ? WHERE order_id = ? AND status != 'paid'",
        (payment_id, card, expires_in, utcnow_iso(), order_id),
    )


async def fail_hamyon_payment(order_id: str):
    await db.execute(
        "UPDATE hamyon_payments SET status = 'failed', updated_at = ? "
        "WHERE order_id = ? AND status = 'pending' AND payment_id IS NULL",
        (utcnow_iso(), order_id),
    )


async def prepare_hamyon_payment(order_id: str, payment_id: str, amount: int) -> bool:
    async with db.transaction() as conn:
        payment = await _fetchone(
            conn, "SELECT * FROM hamyon_payments WHERE order_id = ?", (order_id,)
        )
        if payment is None or int(payment["amount"]) != amount:
            return False
        other = await _fetchone(
            conn,
            "SELECT order_id FROM hamyon_payments WHERE payment_id = ?",
            (payment_id,),
        )
        if other is not None and other["order_id"] != order_id:
            return False
        await conn.execute(
            "UPDATE hamyon_payments SET payment_id = ?, updated_at = ? "
            "WHERE order_id = ? AND status != 'paid'",
            (payment_id, utcnow_iso(), order_id),
        )
        return True


async def cancel_hamyon_payment(payment_id: str):
    await db.execute(
        "UPDATE hamyon_payments SET status = 'cancelled', updated_at = ? "
        "WHERE payment_id = ? AND status != 'paid'",
        (utcnow_iso(), payment_id),
    )


async def complete_hamyon_payment(payment_id: str, amount: int):
    """To'lovni atomar tasdiqlaydi; qayta callback balansni takror oshirmaydi."""
    async with db.transaction() as conn:
        payment = await _fetchone(
            conn, "SELECT * FROM hamyon_payments WHERE payment_id = ?", (payment_id,)
        )
        if payment is None or int(payment["amount"]) != amount:
            return None

        user = await _fetchone(
            conn,
            "SELECT tg_id, balance, full_name, username FROM users WHERE id = ?",
            (payment["user_id"],),
        )
        if user is None:
            return None

        if payment["status"] == "paid":
            return {
                "credited": False,
                "user_tg": int(user["tg_id"]),
                "user_name": user["full_name"] or "",
                "username": user["username"],
                "amount": amount,
                "balance": int(user["balance"]),
                "payment_id": payment_id,
                "purpose": str(payment["purpose"] or "topup"),
            }

        now = utcnow_iso()
        await conn.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ?",
            (amount, payment["user_id"]),
        )
        await conn.execute(
            "INSERT INTO transactions (user_id, amount, type, description, created_at) "
            "VALUES (?, ?, 'topup', ?, ?)",
            (payment["user_id"], amount, f"HamyonAPI to'lovi {payment_id}", now),
        )
        await conn.execute(
            "UPDATE hamyon_payments SET status = 'paid', paid_at = ?, updated_at = ? "
            "WHERE payment_id = ?",
            (now, now, payment_id),
        )
        updated = await _fetchone(
            conn, "SELECT balance FROM users WHERE id = ?", (payment["user_id"],)
        )
        return {
            "credited": True,
            "user_tg": int(user["tg_id"]),
            "user_name": user["full_name"] or "",
            "username": user["username"],
            "amount": amount,
            "balance": int(updated["balance"]),
            "payment_id": payment_id,
            "purpose": str(payment["purpose"] or "topup"),
        }


async def create_topup(user_id: int, amount: int, receipt_type: str, receipt_value: str):
    return await db.execute(
        "INSERT INTO topups (user_id, amount, receipt_type, receipt_value, status, created_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?)",
        (user_id, amount, receipt_type, receipt_value, utcnow_iso()),
    )


async def get_topup(topup_id: int):
    return await db.fetchone("SELECT * FROM topups WHERE id = ?", (topup_id,))


async def get_topup_details(topup_id: int):
    return await db.fetchone(
        "SELECT t.*, u.tg_id AS user_tg, u.full_name AS user_name, u.username "
        "FROM topups t JOIN users u ON u.id = t.user_id WHERE t.id = ?",
        (topup_id,),
    )


async def review_topup(topup_id: int, approve: bool, admin_tg_id: int):
    async with db.transaction() as conn:
        topup = await _fetchone(
            conn, "SELECT * FROM topups WHERE id = ?", (topup_id,)
        )
        if topup is None:
            raise ServiceError("To'lov topilmadi")
        if topup["status"] != "pending":
            raise ServiceError("Bu to'lov allaqachon ko'rib chiqilgan")

        now = utcnow_iso()
        if approve:
            await conn.execute(
                "UPDATE users SET balance = balance + ? WHERE id = ?",
                (topup["amount"], topup["user_id"]),
            )
            await conn.execute(
                "INSERT INTO transactions "
                "(user_id, amount, type, description, created_at) "
                "VALUES (?, ?, 'topup', ?, ?)",
                (
                    topup["user_id"],
                    topup["amount"],
                    f"Balans to'ldirish #{topup_id}",
                    now,
                ),
            )
        status = "approved" if approve else "rejected"
        await conn.execute(
            "UPDATE topups SET status = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ?",
            (status, admin_tg_id, now, topup_id),
        )
        user = await _fetchone(
            conn, "SELECT tg_id, full_name FROM users WHERE id = ?", (topup["user_id"],)
        )
        return {
            "id": topup_id,
            "approved": approve,
            "amount": int(topup["amount"]),
            "user_tg": user["tg_id"] if user else None,
            "user_name": user["full_name"] if user else "",
        }


async def list_pending_topups(limit: int = 10):
    return await db.fetchall(
        "SELECT t.*, u.tg_id AS user_tg, u.full_name AS user_name, u.username "
        "FROM topups t JOIN users u ON u.id = t.user_id "
        "WHERE t.status = 'pending' ORDER BY t.id ASC LIMIT ?",
        (limit,),
    )


def reveal_card(value: str) -> str:
    """Bazadagi shifrlangan karta raqamini ochadi.

    Eski (shifrlanmagan) yozuvlar uchun qiymat o'zgarishsiz qaytadi.
    """
    try:
        return crypto.decrypt(value)
    except ValueError:
        return value


async def create_withdrawal(user_id: int, amount: int, card_number: str):
    async with db.transaction() as conn:
        user = await _fetchone(conn, "SELECT * FROM users WHERE id = ?", (user_id,))
        if user is None:
            raise ServiceError("Foydalanuvchi topilmadi")
        if not user["is_seller"]:
            raise ServiceError("Faqat sotuvchilar pul yechib olishlari mumkin")
        if user["is_blocked"]:
            raise ServiceError("Hisobingiz bloklangan")
        if user["balance"] < amount:
            raise InsufficientFunds(amount, user["balance"])

        debited = await conn.execute(
            "UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ?",
            (amount, user_id, amount),
        )
        if debited.rowcount == 0:
            await debited.close()
            raise InsufficientFunds(amount, user["balance"])
        await debited.close()

        cursor = await conn.execute(
            "INSERT INTO withdrawals "
            "(user_id, amount, card_number, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (user_id, amount, crypto.encrypt(card_number), utcnow_iso()),
        )
        withdrawal_id = int(cursor.lastrowid or 0)
        await cursor.close()

        await conn.execute(
            "INSERT INTO transactions (user_id, amount, type, description, created_at) "
            "VALUES (?, ?, 'withdraw', ?, ?)",
            (user_id, -amount, f"Pul yechish so'rovi #{withdrawal_id}", utcnow_iso()),
        )
        return withdrawal_id


async def review_withdrawal(withdrawal_id: int, approve: bool, admin_tg_id: int):
    async with db.transaction() as conn:
        withdrawal = await _fetchone(
            conn, "SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,)
        )
        if withdrawal is None:
            raise ServiceError("So'rov topilmadi")
        if withdrawal["status"] != "pending":
            raise ServiceError("Bu so'rov allaqachon ko'rib chiqilgan")

        now = utcnow_iso()
        if not approve:
            await conn.execute(
                "UPDATE users SET balance = balance + ? WHERE id = ?",
                (withdrawal["amount"], withdrawal["user_id"]),
            )
            await conn.execute(
                "INSERT INTO transactions "
                "(user_id, amount, type, description, created_at) "
                "VALUES (?, ?, 'withdraw_refund', ?, ?)",
                (
                    withdrawal["user_id"],
                    withdrawal["amount"],
                    f"Pul yechish #{withdrawal_id} bekor qilindi",
                    now,
                ),
            )

        status = "approved" if approve else "rejected"
        await conn.execute(
            "UPDATE withdrawals SET status = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ?",
            (status, admin_tg_id, now, withdrawal_id),
        )
        user = await _fetchone(
            conn,
            "SELECT tg_id, full_name FROM users WHERE id = ?",
            (withdrawal["user_id"],),
        )
        return {
            "id": withdrawal_id,
            "approved": approve,
            "amount": int(withdrawal["amount"]),
            "card_number": reveal_card(withdrawal["card_number"]),
            "user_tg": user["tg_id"] if user else None,
            "user_name": user["full_name"] if user else "",
        }


async def get_withdrawal(withdrawal_id: int):
    return await db.fetchone(
        "SELECT w.*, u.tg_id AS user_tg, u.full_name AS user_name, u.seller_code "
        "FROM withdrawals w JOIN users u ON u.id = w.user_id WHERE w.id = ?",
        (withdrawal_id,),
    )


async def list_pending_withdrawals(limit: int = 10):
    return await db.fetchall(
        "SELECT w.*, u.tg_id AS user_tg, u.full_name AS user_name, u.seller_code "
        "FROM withdrawals w JOIN users u ON u.id = w.user_id "
        "WHERE w.status = 'pending' ORDER BY w.id ASC LIMIT ?",
        (limit,),
    )


async def get_dispute_details(order_id: int):
    return await db.fetchone(
        "SELECT d.*, o.price, o.penalty, o.status AS order_status, o.account_id, "
        "b.tg_id AS buyer_tg, b.full_name AS buyer_name, "
        "s.tg_id AS seller_tg, s.full_name AS seller_name, s.seller_code "
        "FROM disputes d "
        "JOIN orders o ON o.id = d.order_id "
        "JOIN users b ON b.id = d.buyer_id "
        "JOIN users s ON s.id = o.seller_id "
        "WHERE d.order_id = ?",
        (order_id,),
    )


async def list_pending_disputes(limit: int = 10):
    return await db.fetchall(
        "SELECT d.*, o.price, o.penalty, o.status AS order_status, o.account_id, "
        "b.tg_id AS buyer_tg, b.full_name AS buyer_name, "
        "s.tg_id AS seller_tg, s.full_name AS seller_name, s.seller_code "
        "FROM disputes d "
        "JOIN orders o ON o.id = d.order_id "
        "JOIN users b ON b.id = d.buyer_id "
        "JOIN users s ON s.id = o.seller_id "
        "WHERE d.status = 'pending' ORDER BY d.id ASC LIMIT ?",
        (limit,),
    )


async def admin_stats() -> dict:
    async def scalar(sql: str, params: tuple = ()) -> int:
        row = await db.fetchone(sql, params)
        return int(row[0]) if row and row[0] is not None else 0

    return {
        "users": await scalar("SELECT COUNT(*) FROM users"),
        "sellers": await scalar("SELECT COUNT(*) FROM users WHERE is_seller = 1"),
        "active_accounts": await scalar(
            "SELECT COUNT(*) FROM accounts WHERE status = 'active'"
        ),
        "sold_accounts": await scalar(
            "SELECT COUNT(*) FROM accounts WHERE status = 'sold'"
        ),
        "checking": await scalar(
            "SELECT COUNT(*) FROM orders WHERE status = 'checking'"
        ),
        "completed": await scalar(
            "SELECT COUNT(*) FROM orders WHERE status IN ('completed','auto_completed')"
        ),
        "disputed": await scalar(
            "SELECT COUNT(*) FROM disputes WHERE status = 'pending'"
        ),
        "gmv": await scalar(
            "SELECT COALESCE(SUM(price + penalty),0) FROM orders "
            "WHERE status IN ('completed','auto_completed')"
        ),
        "commission": await scalar(
            "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type = 'commission'"
        ),
        "pending_topups": await scalar(
            "SELECT COUNT(*) FROM topups WHERE status = 'pending'"
        ),
        "pending_withdrawals": await scalar(
            "SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'"
        ),
    }


async def list_recent_sales(limit: int = 20):
    return await db.fetchall(
        "SELECT o.*, b.full_name AS buyer_name, s.full_name AS seller_name, "
        "s.seller_code "
        "FROM orders o "
        "JOIN users b ON b.id = o.buyer_id "
        "JOIN users s ON s.id = o.seller_id "
        "WHERE o.status IN ('completed','auto_completed','refunded') "
        "ORDER BY o.id DESC LIMIT ?",
        (limit,),
    )


async def search_user(query: str):
    query = query.strip().lstrip("@")
    if query.isdigit():
        return await db.fetchone(
            "SELECT * FROM users WHERE tg_id = ? OR id = ?", (int(query), int(query))
        )
    return await db.fetchone(
        "SELECT * FROM users WHERE seller_code = ? OR username = ?",
        (query.upper(), query),
    )


async def set_blocked(user_id: int, blocked: bool) -> None:
    await db.execute(
        "UPDATE users SET is_blocked = ? WHERE id = ?", (1 if blocked else 0, user_id)
    )


async def broadcast_recipients():
    return await db.fetchall("SELECT tg_id FROM users WHERE is_blocked = 0")


async def format_order_created(order) -> str:
    return fmt_dt(order["created_at"])
