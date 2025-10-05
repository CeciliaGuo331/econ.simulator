"""PostgreSQL-backed storage for users and sessions."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from ..data_access.postgres_support import get_pool
from ..data_access.postgres_utils import quote_identifier
from .user_manager import UserRecord
from .validators import validate_email, validate_user_type


@dataclass
class _SchemaConfig:
    schema: str
    users_table: str
    sessions_table: str

    @property
    def qualified_users(self) -> str:
        schema_ident = quote_identifier(self.schema)
        table_ident = quote_identifier(self.users_table)
        return f"{schema_ident}.{table_ident}"

    @property
    def qualified_sessions(self) -> str:
        schema_ident = quote_identifier(self.schema)
        table_ident = quote_identifier(self.sessions_table)
        return f"{schema_ident}.{table_ident}"


class PostgresUserStore:
    """Store user records in PostgreSQL."""

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "public",
        table: str = "users",
        min_pool_size: int = 1,
        max_pool_size: int = 5,
    ) -> None:
        self._dsn = dsn
        self._schema_cfg = _SchemaConfig(
            schema=schema, users_table=table, sessions_table="sessions"
        )
        self._min_pool_size = min_pool_size
        self._max_pool_size = max_pool_size
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return
            pool = await get_pool(
                self._dsn, min_size=self._min_pool_size, max_size=self._max_pool_size
            )
            users_table = self._schema_cfg.qualified_users
            async with pool.acquire() as conn:
                await conn.execute(
                    f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(self._schema_cfg.schema)}"
                )
                await conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {users_table} (
                        email TEXT PRIMARY KEY,
                        password_hash TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        user_type TEXT NOT NULL
                    )
                    """
                )
            self._initialized = True

    async def get_user(self, email: str) -> Optional[UserRecord]:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool_size, max_size=self._max_pool_size
        )
        users_table = self._schema_cfg.qualified_users
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT email, password_hash, created_at, user_type FROM {users_table} WHERE email = $1",
                email,
            )
        if row is None:
            return None
        validate_email(row["email"])
        user_type = validate_user_type(row["user_type"], allow_admin=True)
        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        return UserRecord(
            email=row["email"],
            password_hash=row["password_hash"],
            created_at=created_at,
            user_type=user_type,
        )

    async def save_user(self, record: UserRecord) -> None:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool_size, max_size=self._max_pool_size
        )
        users_table = self._schema_cfg.qualified_users
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {users_table} (email, password_hash, created_at, user_type)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (email) DO UPDATE SET
                    password_hash = EXCLUDED.password_hash,
                    created_at = EXCLUDED.created_at,
                    user_type = EXCLUDED.user_type
                """,
                record.email,
                record.password_hash,
                record.created_at,
                record.user_type,
            )

    async def clear(self) -> None:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool_size, max_size=self._max_pool_size
        )
        users_table = self._schema_cfg.qualified_users
        async with pool.acquire() as conn:
            await conn.execute(f"DELETE FROM {users_table}")

    async def list_users(self) -> List[UserRecord]:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool_size, max_size=self._max_pool_size
        )
        users_table = self._schema_cfg.qualified_users
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT email, password_hash, created_at, user_type FROM {users_table} ORDER BY created_at"
            )
        records: List[UserRecord] = []
        for row in rows:
            created_at = row["created_at"]
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            records.append(
                UserRecord(
                    email=row["email"],
                    password_hash=row["password_hash"],
                    created_at=created_at,
                    user_type=validate_user_type(row["user_type"], allow_admin=True),
                )
            )
        return records

    async def delete_user(self, email: str) -> None:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool_size, max_size=self._max_pool_size
        )
        users_table = self._schema_cfg.qualified_users
        async with pool.acquire() as conn:
            await conn.execute(f"DELETE FROM {users_table} WHERE email = $1", email)


class PostgresSessionStore:
    """Persist session tokens in PostgreSQL."""

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "public",
        table: str = "sessions",
        users_table: str = "users",
        min_pool_size: int = 1,
        max_pool_size: int = 5,
    ) -> None:
        self._dsn = dsn
        self._schema_cfg = _SchemaConfig(
            schema=schema, users_table=users_table, sessions_table=table
        )
        self._min_pool_size = min_pool_size
        self._max_pool_size = max_pool_size
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            pool = await get_pool(
                self._dsn, min_size=self._min_pool_size, max_size=self._max_pool_size
            )
            sessions_table = self._schema_cfg.qualified_sessions
            users_table = self._schema_cfg.qualified_users
            async with pool.acquire() as conn:
                await conn.execute(
                    f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(self._schema_cfg.schema)}"
                )
                await conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {sessions_table} (
                        token TEXT PRIMARY KEY,
                        email TEXT NOT NULL REFERENCES {users_table} (email) ON DELETE CASCADE,
                        created_at TIMESTAMPTZ NOT NULL,
                        last_accessed TIMESTAMPTZ NOT NULL,
                        expires_at TIMESTAMPTZ
                    )
                    """
                )
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {quote_identifier(self._schema_cfg.sessions_table + '_email_idx')} ON {sessions_table} (email)"
                )
            self._initialized = True

    async def create_session(self, email: str) -> str:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool_size, max_size=self._max_pool_size
        )
        token = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        sessions_table = self._schema_cfg.qualified_sessions
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {sessions_table} (token, email, created_at, last_accessed)
                VALUES ($1, $2, $3, $3)
                """,
                token,
                email,
                now,
            )
        return token

    async def get_email(self, token: str) -> Optional[str]:
        if not token:
            return None
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool_size, max_size=self._max_pool_size
        )
        sessions_table = self._schema_cfg.qualified_sessions
        now = datetime.now(timezone.utc)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT email FROM {sessions_table} WHERE token = $1",
                token,
            )
            if row is not None:
                await conn.execute(
                    f"UPDATE {sessions_table} SET last_accessed = $2 WHERE token = $1",
                    token,
                    now,
                )
        if row is None:
            return None
        return row["email"]

    async def clear(self) -> None:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool_size, max_size=self._max_pool_size
        )
        sessions_table = self._schema_cfg.qualified_sessions
        async with pool.acquire() as conn:
            await conn.execute(f"DELETE FROM {sessions_table}")

    async def revoke_user(self, email: str) -> None:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool_size, max_size=self._max_pool_size
        )
        sessions_table = self._schema_cfg.qualified_sessions
        async with pool.acquire() as conn:
            await conn.execute(f"DELETE FROM {sessions_table} WHERE email = $1", email)
