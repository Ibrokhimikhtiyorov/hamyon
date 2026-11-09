import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER NOT NULL UNIQUE,
    full_name TEXT NOT NULL DEFAULT '',
    username TEXT,
    phone TEXT,
    seller_code TEXT UNIQUE,
    is_seller INTEGER NOT NULL DEFAULT 0,
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_blocked INTEGER NOT NULL DEFAULT 0,
    balance INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    login_enc TEXT NOT NULL,
    login_hash TEXT NOT NULL,
    password_enc TEXT NOT NULL,
    price INTEGER NOT NULL CHECK(price > 0),
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    sold_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_login_hash ON accounts(login_hash);
CREATE INDEX IF NOT EXISTS idx_accounts_seller_status ON accounts(seller_id, status);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    seller_id INTEGER NOT NULL REFERENCES users(id),
    buyer_id INTEGER NOT NULL REFERENCES users(id),
    price INTEGER NOT NULL,
    penalty INTEGER NOT NULL DEFAULT 0,
    penalty_ticks INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'checking',
    check_deadline TEXT NOT NULL,
    auto_complete_at TEXT NOT NULL,
    last_penalty_at TEXT,
    reminder_5_sent INTEGER NOT NULL DEFAULT 0,
    reminder_1_sent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_buyer ON orders(buyer_id);
CREATE INDEX IF NOT EXISTS idx_orders_seller ON orders(seller_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    order_id INTEGER REFERENCES orders(id) ON DELETE SET NULL,
    amount INTEGER NOT NULL,
    type TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id);

CREATE TABLE IF NOT EXISTS topups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount INTEGER NOT NULL,
    receipt_type TEXT NOT NULL,
    receipt_value TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    reviewed_by INTEGER,
    reviewed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_topups_status ON topups(status);

-- HamyonAPI avtomatik to'lovlari. order_id to'lov yaratilishidan oldin saqlanadi,
-- chunki prepare callback API javobidan oldin kelishi mumkin.
CREATE TABLE IF NOT EXISTS hamyon_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL UNIQUE,
    payment_id TEXT UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount INTEGER NOT NULL CHECK(amount > 0),
    card TEXT,
    expires_in INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    paid_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_hamyon_payments_user ON hamyon_payments(user_id);
CREATE INDEX IF NOT EXISTS idx_hamyon_payments_status ON hamyon_payments(status);

CREATE TABLE IF NOT EXISTS withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount INTEGER NOT NULL,
    card_number TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    reviewed_by INTEGER,
    reviewed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_withdrawals_status ON withdrawals(status);

CREATE TABLE IF NOT EXISTS disputes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id) ON DELETE CASCADE,
    buyer_id INTEGER NOT NULL REFERENCES users(id),
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    resolved_by INTEGER,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_disputes_status ON disputes(status);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_tg INTEGER,
    action TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
"""


async def _ensure_columns(conn: aiosqlite.Connection) -> None:
    migrations = {
        "orders": {
            "reminder_5_sent": "INTEGER NOT NULL DEFAULT 0",
            "reminder_1_sent": "INTEGER NOT NULL DEFAULT 0",
        },
    }
    for table, columns in migrations.items():
        cursor = await conn.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        for name, definition in columns.items():
            if name not in existing:
                await conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                )


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(str(self.path))
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA foreign_keys=ON")
        await self.conn.executescript(SCHEMA)
        await _ensure_columns(self.conn)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    def _connection(self) -> aiosqlite.Connection:
        if self.conn is None:
            raise RuntimeError("Ma'lumotlar bazasi ulanmagan")
        return self.conn

    async def fetchone(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        async with self._lock:
            cursor = await self._connection().execute(sql, params)
            row = await cursor.fetchone()
            await cursor.close()
            return row

    async def fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        async with self._lock:
            cursor = await self._connection().execute(sql, params)
            rows = await cursor.fetchall()
            await cursor.close()
            return list(rows)

    async def execute(self, sql: str, params: tuple = ()) -> int:
        async with self._lock:
            cursor = await self._connection().execute(sql, params)
            await self._connection().commit()
            last_id = cursor.lastrowid
            await cursor.close()
            return int(last_id or 0)

    @asynccontextmanager
    async def transaction(self):
        async with self._lock:
            conn = self._connection()
            try:
                yield conn
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise


db = Database(DB_PATH)
