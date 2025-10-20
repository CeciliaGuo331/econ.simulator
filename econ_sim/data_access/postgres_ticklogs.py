"""基于 PostgreSQL 的每步 Tick 日志持久化存储（交易/历史）。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple

from .models import TickLogEntry
from .postgres_support import get_pool
from .postgres_utils import quote_identifier


class PostgresTickLogStore:
    """用于持久化 TickLogEntry 项以供历史查询。"""

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "public",
        table: str = "tick_logs",
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
        """幂等地创建 Tick 日志表与必要索引。"""
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
                        id SERIAL PRIMARY KEY,
                        simulation_id TEXT NOT NULL,
                        tick INT NOT NULL,
                        day INT NOT NULL,
                        message TEXT NOT NULL,
                        context JSONB,
                        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {quote_identifier(self._table + '_sim_tick_idx')} ON {qualified} (simulation_id, tick)"
                )
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {quote_identifier(self._table + '_sim_day_idx')} ON {qualified} (simulation_id, day)"
                )
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {quote_identifier(self._table + '_sim_time_idx')} ON {qualified} (simulation_id, recorded_at DESC)"
                )
            self._initialized = True

    async def record_many(self, simulation_id: str, logs: List[TickLogEntry]) -> None:
        if not logs:
            return
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        payload = []
        for item in logs:
            ctx = item.context
            # 某些 asyncpg 驱动版本在 executemany 时对 JSONB 类型绑定对输入类型较为敏感；
            # 为了避免出现 "expected str, got dict" 之类的错误，尽量为 JSONB 列传入 JSON 字符串。
            if ctx is None:
                ctx_serialized = None
            else:
                try:
                    ctx_serialized = json.dumps(ctx)
                except Exception:
                    # 如果 json.dumps 无法序列化（例如包含不可序列化对象），
                    # 降级为字符串转换以保证尽力持久化而不抛出致命错误。
                    ctx_serialized = str(ctx)
            payload.append(
                (simulation_id, item.tick, item.day, item.message, ctx_serialized)
            )
        async with pool.acquire() as conn:
            await conn.executemany(
                f"""
                INSERT INTO {qualified} (simulation_id, tick, day, message, context)
                VALUES ($1, $2, $3, $4, $5)
                """,
                payload,
            )

    async def query(
        self,
        simulation_id: str,
        *,
        since_tick: Optional[int] = None,
        until_tick: Optional[int] = None,
        since_day: Optional[int] = None,
        until_day: Optional[int] = None,
        message: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[TickLogEntry]:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"

        clauses: List[str] = ["simulation_id = $1"]
        params: List[Any] = [simulation_id]

        if since_tick is not None:
            clauses.append("tick >= $%d" % (len(params) + 1))
            params.append(since_tick)
        if until_tick is not None:
            clauses.append("tick <= $%d" % (len(params) + 1))
            params.append(until_tick)
        if since_day is not None:
            clauses.append("day >= $%d" % (len(params) + 1))
            params.append(since_day)
        if until_day is not None:
            clauses.append("day <= $%d" % (len(params) + 1))
            params.append(until_day)
        if message:
            clauses.append("message = $%d" % (len(params) + 1))
            params.append(message)

        where_sql = " AND ".join(clauses)
        limit_clause = ""
        if limit is not None and limit > 0:
            limit_clause = f" LIMIT $%d" % (len(params) + 2)
        offset_clause = " OFFSET $%d" % (len(params) + (2 if limit_clause else 1))

        async with pool.acquire() as conn:
            if limit_clause:
                rows = await conn.fetch(
                    f"""
                    SELECT tick, day, message, context
                    FROM {qualified}
                    WHERE {where_sql}
                    ORDER BY tick ASC
                    {limit_clause}
                    {offset_clause}
                    """,
                    *params,
                    limit,
                    offset,
                )
            else:
                rows = await conn.fetch(
                    f"""
                    SELECT tick, day, message, context
                    FROM {qualified}
                    WHERE {where_sql}
                    ORDER BY tick ASC
                    {offset_clause}
                    """,
                    *params,
                    offset,
                )

        return [
            TickLogEntry(
                tick=row["tick"],
                day=row["day"],
                message=row["message"],
                context=dict(row["context"]) if row["context"] is not None else {},
            )
            for row in rows
        ]
