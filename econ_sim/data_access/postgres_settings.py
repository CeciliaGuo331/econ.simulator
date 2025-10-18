"""PostgreSQL-backed simulation settings store."""

from __future__ import annotations

import asyncio
from typing import Dict, Optional

from .postgres_support import get_pool
from .postgres_utils import quote_identifier


class PostgresSimulationSettingsStore:
    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "public",
        table: str = "simulation_settings",
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
                        simulation_id TEXT PRIMARY KEY,
                        script_limit INTEGER
                    )
                    """
                )
            self._initialized = True

    async def set_script_limit(self, simulation_id: str, limit: int) -> None:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {qualified} (simulation_id, script_limit)
                VALUES ($1, $2)
                ON CONFLICT (simulation_id) DO UPDATE SET script_limit = EXCLUDED.script_limit
                """,
                simulation_id,
                limit,
            )

    async def delete_script_limit(self, simulation_id: str) -> None:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        async with pool.acquire() as conn:
            await conn.execute(
                f"DELETE FROM {qualified} WHERE simulation_id = $1",
                simulation_id,
            )

    async def get_script_limit(self, simulation_id: str) -> Optional[int]:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT script_limit FROM {qualified} WHERE simulation_id = $1",
                simulation_id,
            )
        if row is None:
            return None
        return row["script_limit"]

    async def list_script_limits(self) -> Dict[str, int]:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT simulation_id, script_limit FROM {qualified}"
            )
        return {row["simulation_id"]: row["script_limit"] for row in rows}

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

    async def close(self) -> None:
        """Close any internal pools held by this store."""
        try:
            from .postgres_support import close_pool

            await close_pool(
                self._dsn, min_size=self._min_pool, max_size=self._max_pool
            )
        except Exception:
            # best-effort
            return

    # compatibility alias
    async def shutdown(self) -> None:
        await self.close()
