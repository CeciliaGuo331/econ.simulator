"""用户脚本执行相关的工具集。"""

from __future__ import annotations

import os
from typing import Optional

from ..data_access.postgres_settings import PostgresSimulationSettingsStore
from .postgres_store import PostgresScriptStore
from .registry import ScriptRegistry
from .sandbox import DEFAULT_SANDBOX_TIMEOUT, shutdown_process_pool
import asyncio
import time


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


def reset_script_registry() -> None:
    """Recreate the module-level script_registry instance.

    Intended for test teardown to ensure each test module can start with a
    fresh registry without relying on import-time singletons.
    """
    global script_registry
    # Attempt to close resources held by the existing registry's stores if
    # they expose a close/shutdown coroutine. Best-effort to avoid leaving
    # connection pools open across test modules.
    try:
        old = script_registry
        store = getattr(old, "_store", None)
        limit_store = getattr(old, "_limit_store", None)

        # First: ensure process pool is shutdown to avoid worker processes
        # holding references to DB pools or other resources.
        try:
            shutdown_process_pool(wait=True, aggressive_kill=True)
        except Exception:
            # best-effort
            pass

        def _sync_close(obj, timeout: float = 2.0):
            if obj is None:
                return
            for name in ("close", "shutdown"):
                meth = getattr(obj, name, None)
                if not callable(meth):
                    continue
                try:
                    result = meth()
                except Exception:
                    return
                # If the method returned a coroutine, try to run it synchronously
                # if there's no running loop; otherwise attempt to schedule it
                # on the running loop or use run_coroutine_threadsafe if the
                # loop is running in another thread.
                if result is not None and hasattr(result, "__await__"):
                    try:
                        # If there's no running loop in this thread, run the
                        # coroutine directly with asyncio.run which will block
                        # until completion.
                        try:
                            loop = asyncio.get_running_loop()
                        except RuntimeError:
                            loop = None

                        if loop is None:
                            try:
                                asyncio.run(result)
                            except Exception:
                                # best-effort: ignore failures during close
                                pass
                        else:
                            # If there is a running loop in this thread, we cannot
                            # call asyncio.run. Instead, run the coroutine in a
                            # separate thread using asyncio.run (new loop) and wait
                            # for that thread to finish up to `timeout` seconds.
                            import threading

                            def _run_coro():
                                try:
                                    asyncio.run(result)
                                except Exception:
                                    # swallow errors during teardown
                                    pass

                            t = threading.Thread(target=_run_coro, daemon=True)
                            t.start()
                            t.join(timeout)
                            # If thread is still alive after timeout, we give up
                            # (best-effort cleanup only).
                    except Exception:
                        pass
                    except Exception:
                        pass
                return

        try:
            _sync_close(store)
        except Exception:
            pass
        try:
            _sync_close(limit_store)
        except Exception:
            pass
    except Exception:
        # ignore cleanup errors - reset will still proceed
        pass

    script_registry = _build_registry()


__all__ = ["script_registry", "ScriptRegistry"]
