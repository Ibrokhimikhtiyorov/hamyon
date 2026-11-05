import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import aiosqlite
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

from utils import utcnow_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS fsm_states (
    key TEXT PRIMARY KEY,
    state TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fsm_data (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class SQLiteStorage(BaseStorage):
    """FSM holatini SQLite'da saqlaydi, restartdan keyin ham yo'qolmaydi."""

    def __init__(self, path: Path):
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(key: StorageKey) -> str:
        return json.dumps(
            [
                key.bot_id,
                key.chat_id,
                key.user_id,
                key.thread_id,
                key.business_connection_id,
                key.destiny,
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _state_value(state: StateType) -> str | None:
        if state is None:
            return None
        if isinstance(state, State):
            return state.state
        return str(state)

    def _connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("FSM bazasi ulanmagan")
        return self._conn

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(str(self.path))
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = self._state_value(state)
        async with self._lock:
            await self._connection().execute(
                "INSERT INTO fsm_states (key, state, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET state = excluded.state, "
                "updated_at = excluded.updated_at",
                (self._key(key), value, utcnow_iso()),
            )
            await self._connection().commit()

    async def get_state(self, key: StorageKey) -> str | None:
        async with self._lock:
            cursor = await self._connection().execute(
                "SELECT state FROM fsm_states WHERE key = ?", (self._key(key),)
            )
            row = await cursor.fetchone()
            await cursor.close()
            return row[0] if row and row[0] else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(data), ensure_ascii=False, default=str)
        async with self._lock:
            await self._connection().execute(
                "INSERT INTO fsm_data (key, data, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET data = excluded.data, "
                "updated_at = excluded.updated_at",
                (self._key(key), payload, utcnow_iso()),
            )
            await self._connection().commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with self._lock:
            cursor = await self._connection().execute(
                "SELECT data FROM fsm_data WHERE key = ?", (self._key(key),)
            )
            row = await cursor.fetchone()
            await cursor.close()
        if not row or not row[0]:
            return {}
        try:
            return dict(json.loads(row[0]))
        except (ValueError, TypeError):
            return {}

    async def update_data(self, key: StorageKey, data: Mapping[str, Any]) -> dict[str, Any]:
        async with self._lock:
            cursor = await self._connection().execute(
                "SELECT data FROM fsm_data WHERE key = ?", (self._key(key),)
            )
            row = await cursor.fetchone()
            await cursor.close()
            current: dict[str, Any] = {}
            if row and row[0]:
                try:
                    current = dict(json.loads(row[0]))
                except (ValueError, TypeError):
                    current = {}
            current.update(data)
            payload = json.dumps(current, ensure_ascii=False, default=str)
            await self._connection().execute(
                "INSERT INTO fsm_data (key, data, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET data = excluded.data, "
                "updated_at = excluded.updated_at",
                (self._key(key), payload, utcnow_iso()),
            )
            await self._connection().commit()
            return current.copy()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
