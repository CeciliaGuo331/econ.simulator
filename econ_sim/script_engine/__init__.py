"""用户脚本执行相关的工具集。"""

from __future__ import annotations

import os
from typing import Optional
import logging

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


# 内部持有的真实实例（可能为 None，lazy init）
_registry_instance: Optional[ScriptRegistry] = None


def get_script_registry() -> ScriptRegistry:
    """Return the module-level ScriptRegistry, creating it lazily if needed."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = _build_registry()
    return _registry_instance


class _LazyRegistryProxy:
    """Proxy object used so existing imports of `script_registry` keep working.

    Attribute access is forwarded to the real registry produced by
    `get_script_registry()`.
    """

    def __getattr__(self, item: str):
        real = get_script_registry()
        return getattr(real, item)

    def __await__(self):
        # make the proxy awaitable if underlying object is awaitable
        return get_script_registry().__await__()


# Backwards-compatible module-level name used across the codebase. This is a
# proxy so imports like `from econ_sim.script_engine import script_registry`
# remain valid while initialization is lazy.
script_registry: _LazyRegistryProxy = _LazyRegistryProxy()


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
            # honor environment override for aggressive termination timeout
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
                                logging.getLogger(__name__).debug(
                                    "_sync_close: running coroutine close synchronously (no loop)"
                                )
                                asyncio.run(result)
                            except Exception:
                                logging.getLogger(__name__).exception(
                                    "_sync_close: coroutine close raised"
                                )
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
                                    logging.getLogger(__name__).exception(
                                        "_sync_close: coroutine close in thread raised"
                                    )

                            t = threading.Thread(target=_run_coro, daemon=True)
                            t.start()
                            t.join(timeout)
                            if t.is_alive():
                                logging.getLogger(__name__).warning(
                                    "_sync_close: close did not complete within %s seconds",
                                    timeout,
                                )
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

    # Recreate the registry instance and leave the module-level proxy in place
    global _registry_instance
    try:
        _registry_instance = _build_registry()
    except Exception:
        logging.getLogger(__name__).exception(
            "reset_script_registry: failed to rebuild registry"
        )
        _registry_instance = None


__all__ = ["script_registry", "ScriptRegistry"]
