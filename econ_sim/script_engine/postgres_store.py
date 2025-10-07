"""PostgreSQL-backed script storage."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from ..data_access.models import AgentKind
from ..data_access.postgres_support import get_pool
from ..data_access.postgres_utils import quote_identifier
from .registry import ScriptMetadata


@dataclass
class StoredScript:
    metadata: ScriptMetadata
    code: str


class PostgresScriptStore:
    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "public",
        table: str = "scripts",
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
                        script_id UUID PRIMARY KEY,
                        simulation_id TEXT,
                        user_id TEXT NOT NULL,
                        description TEXT,
                        created_at TIMESTAMPTZ NOT NULL,
                        code TEXT NOT NULL,
                        code_version UUID NOT NULL,
                        agent_kind TEXT NOT NULL,
                        entity_id TEXT NOT NULL,
                        last_failure_at TIMESTAMPTZ,
                        last_failure_reason TEXT
                    )
                    """
                )
                await conn.execute(
                    f"ALTER TABLE {qualified} ALTER COLUMN simulation_id DROP NOT NULL"
                )
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {quote_identifier(self._table + '_simulation_idx')} ON {qualified} (simulation_id)"
                )
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {quote_identifier(self._table + '_user_idx')} ON {qualified} (user_id)"
                )
            self._initialized = True

    async def save_script(self, metadata: ScriptMetadata, code: str) -> None:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        script_id = uuid.UUID(metadata.script_id)
        code_version = uuid.UUID(metadata.code_version)
        created_at = (
            metadata.created_at
            if isinstance(metadata.created_at, datetime)
            else datetime.fromisoformat(str(metadata.created_at))
        )
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {qualified} (script_id, simulation_id, user_id, description, created_at, code, code_version, agent_kind, entity_id, last_failure_at, last_failure_reason)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (script_id) DO UPDATE SET
                    simulation_id = EXCLUDED.simulation_id,
                    user_id = EXCLUDED.user_id,
                    description = EXCLUDED.description,
                    created_at = EXCLUDED.created_at,
                    code = EXCLUDED.code,
                    code_version = EXCLUDED.code_version,
                    agent_kind = EXCLUDED.agent_kind,
                    entity_id = EXCLUDED.entity_id,
                    last_failure_at = EXCLUDED.last_failure_at,
                    last_failure_reason = EXCLUDED.last_failure_reason
                """,
                script_id,
                metadata.simulation_id,
                metadata.user_id,
                metadata.description,
                created_at,
                code,
                code_version,
                metadata.agent_kind.value,
                metadata.entity_id,
                metadata.last_failure_at,
                metadata.last_failure_reason,
            )

    async def fetch_simulation_scripts(self, simulation_id: str) -> List[StoredScript]:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT script_id, simulation_id, user_id, description, created_at, code, code_version, agent_kind, entity_id, last_failure_at, last_failure_reason
                FROM {qualified}
                WHERE simulation_id = $1
                ORDER BY created_at
                """,
                simulation_id,
            )
        scripts: List[StoredScript] = []
        for row in rows:
            metadata = ScriptMetadata(
                script_id=str(row["script_id"]),
                simulation_id=row["simulation_id"],
                user_id=row["user_id"],
                description=row["description"],
                created_at=row["created_at"],
                code_version=str(row["code_version"]),
                agent_kind=AgentKind(row["agent_kind"]),
                entity_id=row["entity_id"],
                last_failure_at=row["last_failure_at"],
                last_failure_reason=row["last_failure_reason"],
            )
            scripts.append(StoredScript(metadata=metadata, code=row["code"]))
        return scripts

    async def fetch_user_scripts(self, user_id: str) -> List[StoredScript]:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT script_id, simulation_id, user_id, description, created_at, code, code_version, agent_kind, entity_id, last_failure_at, last_failure_reason
                FROM {qualified}
                WHERE user_id = $1
                ORDER BY created_at
                """,
                user_id,
            )
        scripts: List[StoredScript] = []
        for row in rows:
            metadata = ScriptMetadata(
                script_id=str(row["script_id"]),
                simulation_id=row["simulation_id"],
                user_id=row["user_id"],
                description=row["description"],
                created_at=row["created_at"],
                code_version=str(row["code_version"]),
                agent_kind=AgentKind(row["agent_kind"]),
                entity_id=row["entity_id"],
                last_failure_at=row["last_failure_at"],
                last_failure_reason=row["last_failure_reason"],
            )
            scripts.append(StoredScript(metadata=metadata, code=row["code"]))
        return scripts

    async def update_simulation_binding(
        self, script_id: str, simulation_id: Optional[str]
    ) -> bool:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE {qualified} SET simulation_id = $2 WHERE script_id = $1 RETURNING script_id",
                uuid.UUID(script_id),
                simulation_id,
            )
        return row is not None

    async def list_all_metadata(self) -> List[ScriptMetadata]:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT script_id, simulation_id, user_id, description, created_at, code_version, agent_kind, entity_id, last_failure_at, last_failure_reason
                FROM {qualified}
                ORDER BY created_at
                """
            )
        return [
            ScriptMetadata(
                script_id=str(row["script_id"]),
                simulation_id=row["simulation_id"],
                user_id=row["user_id"],
                description=row["description"],
                created_at=row["created_at"],
                code_version=str(row["code_version"]),
                agent_kind=AgentKind(row["agent_kind"]),
                entity_id=row["entity_id"],
                last_failure_at=row["last_failure_at"],
                last_failure_reason=row["last_failure_reason"],
            )
            for row in rows
        ]

    async def delete_script(self, script_id: str) -> bool:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"DELETE FROM {qualified} WHERE script_id = $1 RETURNING script_id",
                uuid.UUID(script_id),
            )
        return row is not None

    async def delete_by_user(self, user_id: str) -> List[Tuple[Optional[str], str]]:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"DELETE FROM {qualified} WHERE user_id = $1 RETURNING simulation_id, script_id",
                user_id,
            )
        return [(row["simulation_id"], str(row["script_id"])) for row in rows]

    async def detach_simulation(self, simulation_id: str) -> List[str]:
        await self._ensure_schema()
        pool = await get_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified = f"{schema_ident}.{table_ident}"
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"UPDATE {qualified} SET simulation_id = NULL WHERE simulation_id = $1 RETURNING script_id",
                simulation_id,
            )
        return [str(row["script_id"]) for row in rows]

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

    async def update_failure_status(
        self,
        script_id: str,
        failure_at: Optional[datetime],
        failure_reason: Optional[str],
    ) -> None:
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
                UPDATE {qualified}
                SET last_failure_at = $2, last_failure_reason = $3
                WHERE script_id = $1
                """,
                uuid.UUID(script_id),
                failure_at,
                failure_reason,
            )
