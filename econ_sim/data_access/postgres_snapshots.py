"""PostgreSQL-backed agent snapshots (draft).

This module defines a minimal store for per-agent snapshots to support future
state persistence beyond the monolithic world_state snapshot. It is opt-in and
not yet wired into DataAccessLayer.
"""

from __future__ import annotations

import asyncio
from typing import Iterable, List, Optional

from .models import AgentSnapshotRecord
from .postgres_support import get_pool
from .postgres_utils import quote_identifier


class PostgresAgentSnapshotStore:
    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "public",
        table: str = "agent_snapshots",
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
                        id SERIAL PRIMARY KEY,
                        simulation_id TEXT NOT NULL,
                        tick INTEGER NOT NULL,
                        day INTEGER NOT NULL,
                        agent_kind TEXT NOT NULL,
                        entity_id TEXT NOT NULL,
                        payload JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
                    )
                    """
                )
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {quote_identifier(self._table + '_sim_tick_idx')} ON {qualified} (simulation_id, tick DESC)"
                )
            self._initialized = True

    async def record_many(self, simulation_id: str, records: Iterable[AgentSnapshotRecord]) -> int:
        await self._ensure_schema()
        batch = list(records)
        if not batch:
            return 0
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        rows = [
            (
                simulation_id,
                r.tick,
                r.day,
                r.agent_kind.value,
                str(r.entity_id),
                r.model_dump()["payload"],
            )
            for r in batch
        ]
        async with pool.acquire() as conn:
            await conn.executemany(
                f"""
                INSERT INTO {qualified} (simulation_id, tick, day, agent_kind, entity_id, payload)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                """,
                rows,
            )
        return len(rows)

    async def query(
        self,
        simulation_id: str,
        *,
        agent_kind: Optional[str] = None,
        entity_id: Optional[str] = None,
        since_tick: Optional[int] = None,
        until_tick: Optional[int] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[AgentSnapshotRecord]:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"

        clauses = ["simulation_id = $1"]
        params = [simulation_id]
        if agent_kind:
            clauses.append("agent_kind = $2")
            params.append(agent_kind)
        if entity_id:
            clauses.append(f"entity_id = ${len(params)+1}")
            params.append(entity_id)
        if since_tick is not None:
            clauses.append(f"tick >= ${len(params)+1}")
            params.append(since_tick)
        if until_tick is not None:
            clauses.append(f"tick <= ${len(params)+1}")
            params.append(until_tick)

        where_sql = " AND ".join(clauses)
        limit_sql = f"LIMIT {int(limit)}" if limit and limit > 0 else ""
        offset_sql = f"OFFSET {int(offset)}" if offset and offset > 0 else ""

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT tick, day, agent_kind, entity_id, payload
                FROM {qualified}
                WHERE {where_sql}
                ORDER BY tick DESC
                {limit_sql}
                {offset_sql}
                """,
                *params,
            )
        out: List[AgentSnapshotRecord] = []
        for row in rows:
            out.append(
                AgentSnapshotRecord(
                    tick=row["tick"],
                    day=row["day"],
                    agent_kind=row["agent_kind"],
                    entity_id=row["entity_id"],
                    payload=dict(row["payload"]),
                )
            )
        return out
