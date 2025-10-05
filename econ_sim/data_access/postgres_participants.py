"""PostgreSQL-backed participant registry."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List

from .postgres_support import get_pool
from .postgres_utils import quote_identifier


class PostgresParticipantStore:
    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "public",
        table: str = "simulation_participants",
        min_pool_size: int = 1,
        max_pool_size: int = 5,
    ) -> None:
        self._dsn = dsn
        self._schema = schema
        self._table = table
        self._min_pool = min_pool_size
        self._max_pool = max_pool_size
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            pool = await get_pool(
                self._dsn, min_size=self._min_pool, max_size=self._max_pool
            )
            schema_ident = quote_identifier(self._schema)
            table_ident = quote_identifier(self._table)
            qualified = f"{schema_ident}.{table_ident}"
            async with pool.acquire() as conn:
                await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_ident}")
                await conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {qualified} (
                        simulation_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        joined_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
                        PRIMARY KEY (simulation_id, user_id)
                    )
                    """
                )
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {quote_identifier(self._table + '_user_idx')} ON {qualified} (user_id)"
                )
            self._initialized = True

    async def register(self, simulation_id: str, user_id: str) -> None:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        now = datetime.now(timezone.utc)
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {qualified} (simulation_id, user_id, joined_at)
                VALUES ($1, $2, $3)
                ON CONFLICT (simulation_id, user_id) DO UPDATE SET joined_at = EXCLUDED.joined_at
                """,
                simulation_id,
                user_id,
                now,
            )

    async def list_participants(self, simulation_id: str) -> List[str]:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT user_id FROM {qualified} WHERE simulation_id = $1 ORDER BY user_id",
                simulation_id,
            )
        return [row["user_id"] for row in rows]

    async def remove_simulation(self, simulation_id: str) -> int:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"DELETE FROM {qualified} WHERE simulation_id = $1 RETURNING user_id",
                simulation_id,
            )
        return len(rows)

    async def remove_user(self, user_id: str) -> int:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"DELETE FROM {qualified} WHERE user_id = $1 RETURNING simulation_id",
                user_id,
            )
        return len(rows)

    async def clear(self) -> None:
        if not self._initialized:
            return
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        async with pool.acquire() as conn:
            await conn.execute(f"DELETE FROM {qualified}")
