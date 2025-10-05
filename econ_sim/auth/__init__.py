"""用户认证相关功能入口。"""

import os
from typing import Optional

from .user_manager import InMemorySessionStore, InMemoryUserStore, UserManager

try:  # pragma: no cover - optional dependency
    from .postgres_store import PostgresSessionStore, PostgresUserStore
except Exception:  # pragma: no cover - asyncpg or other dependency missing
    PostgresSessionStore = None  # type: ignore
    PostgresUserStore = None  # type: ignore


def _build_user_manager() -> UserManager:
    dsn = os.getenv("ECON_SIM_POSTGRES_DSN")
    schema = os.getenv("ECON_SIM_POSTGRES_SCHEMA", "public")
    min_pool = int(os.getenv("ECON_SIM_POSTGRES_MIN_POOL", "1"))
    max_pool = int(os.getenv("ECON_SIM_POSTGRES_MAX_POOL", "5"))

    if dsn and PostgresUserStore and PostgresSessionStore:
        user_store = PostgresUserStore(
            dsn,
            schema=schema,
            min_pool_size=min_pool,
            max_pool_size=max_pool,
        )
        session_store = PostgresSessionStore(
            dsn,
            schema=schema,
            users_table="users",
            table="sessions",
            min_pool_size=min_pool,
            max_pool_size=max_pool,
        )
        return UserManager(user_store, session_store)

    return UserManager(InMemoryUserStore(), InMemorySessionStore())


user_manager = _build_user_manager()

__all__ = ["user_manager", "UserManager"]
