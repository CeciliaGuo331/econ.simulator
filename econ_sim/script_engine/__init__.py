"""用户脚本执行相关的工具集。"""

from __future__ import annotations

import os

from .postgres_store import PostgresScriptStore
from .registry import ScriptRegistry


def _build_registry() -> ScriptRegistry:
    dsn = os.getenv("ECON_SIM_POSTGRES_DSN")
    if not dsn:
        return ScriptRegistry()

    schema = os.getenv("ECON_SIM_POSTGRES_SCHEMA", "public")
    table = os.getenv("ECON_SIM_POSTGRES_SCRIPT_TABLE", "scripts")
    min_pool = int(os.getenv("ECON_SIM_POSTGRES_MIN_POOL", "1"))
    max_pool = int(os.getenv("ECON_SIM_POSTGRES_MAX_POOL", "5"))

    store = PostgresScriptStore(
        dsn,
        schema=schema,
        table=table,
        min_pool_size=min_pool,
        max_pool_size=max_pool,
    )
    return ScriptRegistry(store=store)


# 全局单例，供 API 与调度器共享。
script_registry = _build_registry()

__all__ = ["script_registry", "ScriptRegistry"]
