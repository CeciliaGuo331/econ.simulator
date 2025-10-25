"""Postgres-based ledger persistence for audit queries."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

from .models import LedgerEntry
from .postgres_support import get_pool
from .postgres_utils import quote_identifier


class PostgresLedgerStore:
    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "public",
        table: str = "ledgers",
        min_pool_size: int = 1,
        max_pool_size: int = 5,
    ) -> None:
        self._dsn = dsn
        self._schema = schema
        self._table = table
        self._min_pool = min_pool_size
        self._max_pool = max_pool_size
        self._initialized = False
        self._init_lock = None

    async def _ensure_schema(self) -> None:
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
                    account_kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    entry_type TEXT NOT NULL,
                    amount NUMERIC,
                    balance_after NUMERIC,
                    reference TEXT,
                    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS {quote_identifier(self._table + '_sim_tick_idx')} ON {qualified} (simulation_id, tick)"
            )
        self._initialized = True

    async def record_many(
        self, simulation_id: str, ledgers: Iterable[LedgerEntry]
    ) -> None:
        items = list(ledgers)
        if not items:
            return
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        payload = []
        for l in items:
            payload.append(
                (
                    simulation_id,
                    int(l.tick),
                    int(l.day),
                    (
                        l.account_kind.value
                        if hasattr(l.account_kind, "value")
                        else str(l.account_kind)
                    ),
                    str(l.entity_id),
                    l.entry_type,
                    float(l.amount) if l.amount is not None else None,
                    float(l.balance_after) if l.balance_after is not None else None,
                    l.reference,
                )
            )
        async with pool.acquire() as conn:
            # use executemany-like behavior with asyncpg
            await conn.executemany(
                f"""
                INSERT INTO {qualified} (simulation_id, tick, day, account_kind, entity_id, entry_type, amount, balance_after, reference)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                """,
                payload,
            )

    async def query(
        self,
        simulation_id: str,
        *,
        since_tick: Optional[int] = None,
        until_tick: Optional[int] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[LedgerEntry]:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        clauses = ["simulation_id = $1"]
        params = [simulation_id]
        if since_tick is not None:
            clauses.append("tick >= $%d" % (len(params) + 1))
            params.append(since_tick)
        if until_tick is not None:
            clauses.append("tick <= $%d" % (len(params) + 1))
            params.append(until_tick)
        where_sql = " AND ".join(clauses)
        limit_clause = ""
        if limit is not None and limit > 0:
            limit_clause = f" LIMIT $%d" % (len(params) + 2)
        offset_clause = " OFFSET $%d" % (len(params) + (2 if limit_clause else 1))
        async with pool.acquire() as conn:
            if limit_clause:
                rows = await conn.fetch(
                    f"""
                    SELECT tick, day, account_kind, entity_id, entry_type, amount, balance_after, reference
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
                    SELECT tick, day, account_kind, entity_id, entry_type, amount, balance_after, reference
                    FROM {qualified}
                    WHERE {where_sql}
                    ORDER BY tick ASC
                    {offset_clause}
                    """,
                    *params,
                    offset,
                )
        out: List[LedgerEntry] = []
        for r in rows:
            out.append(
                LedgerEntry(
                    tick=r["tick"],
                    day=r["day"],
                    account_kind=r["account_kind"],
                    entity_id=r["entity_id"],
                    entry_type=r["entry_type"],
                    amount=float(r["amount"]) if r["amount"] is not None else None,
                    balance_after=(
                        float(r["balance_after"])
                        if r["balance_after"] is not None
                        else None
                    ),
                    reference=r["reference"],
                )
            )
        return out
