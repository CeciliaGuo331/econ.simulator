"""基于 PostgreSQL 的脚本失败事件持久化存储。"""

from __future__ import annotations

import asyncio
from typing import Iterable, List, Optional

from .models import AgentKind, ScriptFailureRecord
from .postgres_support import get_pool
from .postgres_utils import quote_identifier


class PostgresScriptFailureStore:
    """使用 PostgreSQL 持久化脚本执行失败信息。"""

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "public",
        table: str = "script_failures",
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
                        failure_id UUID PRIMARY KEY,
                        simulation_id TEXT NOT NULL,
                        script_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        agent_kind TEXT NOT NULL,
                        entity_id TEXT NOT NULL,
                        message TEXT NOT NULL,
                        traceback TEXT NOT NULL,
                        occurred_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {quote_identifier(self._table + '_sim_idx')} ON {qualified} (simulation_id, occurred_at DESC)"
                )
            self._initialized = True

    async def record_many(self, records: Iterable[ScriptFailureRecord]) -> None:
        batch = list(records)
        if not batch:
            return
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        payload = [
            (
                record.failure_id,
                record.simulation_id,
                record.script_id,
                record.user_id,
                record.agent_kind.value,
                record.entity_id,
                record.message,
                record.traceback,
                record.occurred_at,
            )
            for record in batch
        ]
        async with pool.acquire() as conn:
            await conn.executemany(
                f"""
                INSERT INTO {qualified} (
                    failure_id,
                    simulation_id,
                    script_id,
                    user_id,
                    agent_kind,
                    entity_id,
                    message,
                    traceback,
                    occurred_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (failure_id) DO NOTHING
                """,
                payload,
            )

    async def list_recent(
        self, simulation_id: str, limit: Optional[int] = None
    ) -> List[ScriptFailureRecord]:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        async with pool.acquire() as conn:
            if limit is not None and limit > 0:
                rows = await conn.fetch(
                    f"""
                    SELECT failure_id, simulation_id, script_id, user_id, agent_kind, entity_id, message, traceback, occurred_at
                    FROM {qualified}
                    WHERE simulation_id = $1
                    ORDER BY occurred_at DESC
                    LIMIT $2
                    """,
                    simulation_id,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    f"""
                    SELECT failure_id, simulation_id, script_id, user_id, agent_kind, entity_id, message, traceback, occurred_at
                    FROM {qualified}
                    WHERE simulation_id = $1
                    ORDER BY occurred_at DESC
                    """,
                    simulation_id,
                )
        return [
            ScriptFailureRecord(
                failure_id=str(row["failure_id"]),
                simulation_id=row["simulation_id"],
                script_id=row["script_id"],
                user_id=row["user_id"],
                agent_kind=AgentKind(row["agent_kind"]),
                entity_id=row["entity_id"],
                message=row["message"],
                traceback=row["traceback"],
                occurred_at=row["occurred_at"],
            )
            for row in rows
        ]

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


__all__ = ["PostgresScriptFailureStore"]
