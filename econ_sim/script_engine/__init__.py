"""用户脚本执行相关的工具集。"""

from __future__ import annotations

import os
from typing import Optional

from ..data_access.postgres_settings import PostgresSimulationSettingsStore
from .postgres_store import PostgresScriptStore
from .registry import ScriptRegistry
from .sandbox import DEFAULT_SANDBOX_TIMEOUT


def _build_registry() -> ScriptRegistry:
    dsn = os.getenv("ECON_SIM_POSTGRES_DSN")
    timeout_env = os.getenv("ECON_SIM_SCRIPT_TIMEOUT_SECONDS")
    try:
        sandbox_timeout = (
            float(timeout_env) if timeout_env is not None else DEFAULT_SANDBOX_TIMEOUT
        )
    except ValueError:
        sandbox_timeout = DEFAULT_SANDBOX_TIMEOUT

    default_limit_env = os.getenv("ECON_SIM_DEFAULT_SCRIPT_LIMIT")
    default_limit: Optional[int]
    if default_limit_env is None:
        default_limit = None
    else:
        try:
            parsed_limit = int(default_limit_env)
            default_limit = parsed_limit if parsed_limit > 0 else None
        except ValueError:
            default_limit = None

    if not dsn:
        return ScriptRegistry(
            sandbox_timeout=sandbox_timeout,
            max_scripts_per_user=default_limit,
        )

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
    settings_store = PostgresSimulationSettingsStore(
        dsn,
        schema=schema,
        min_pool_size=min_pool,
        max_pool_size=max_pool,
    )
    return ScriptRegistry(
        store=store,
        sandbox_timeout=sandbox_timeout,
        max_scripts_per_user=default_limit,
        limit_store=settings_store,
    )


# 全局单例，供 API 与调度器共享。
script_registry = _build_registry()

__all__ = ["script_registry", "ScriptRegistry"]
