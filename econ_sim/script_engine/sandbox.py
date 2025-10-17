"""隔离执行用户策略脚本的轻量沙箱。"""

from __future__ import annotations

import builtins
import concurrent.futures
import json
import multiprocessing
import os
import sys
import threading
import time
import traceback
from collections import deque
from multiprocessing.connection import Connection
from types import MappingProxyType
from typing import Any, Dict, Iterable, Optional, Set

try:  # pragma: no cover - platform compatibility
    import resource
except ImportError:  # pragma: no cover - Windows etc
    resource = None  # type: ignore[assignment]

DEFAULT_SANDBOX_TIMEOUT = 0.75
CPU_TIME_LIMIT_SECONDS = 1
MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024

ALLOWED_MODULES: Set[str] = {
    "math",
    "statistics",
    "random",
    "econ_sim",
    "econ_sim.script_engine",
    "econ_sim.script_engine.user_api",
}

_ALLOWED_BUILTINS: Set[str] = {
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "divmod",
    "enumerate",
    "filter",
    "float",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "NotImplementedError",
    "object",
    "pow",
    "print",
    "range",
    "repr",
    "round",
    "set",
    "sorted",
    "str",
    "sum",
    "tuple",
    "type",
    "ValueError",
    "TypeError",
    "RuntimeError",
    "zip",
    "Exception",
}


class ScriptSandboxError(RuntimeError):
    """脚本在沙箱中执行失败。"""


class ScriptSandboxTimeout(ScriptSandboxError):
    """脚本执行超时。"""


# Process pool for executing scripts (reuse worker processes to avoid spawn/kill costs)
_PROCESS_POOL: Optional[concurrent.futures.ProcessPoolExecutor] = None
_POOL_LOCK = threading.Lock()

# Lightweight in-process metrics
_metrics_lock = threading.Lock()
_script_durations: deque[float] = deque(maxlen=2000)
_timeout_count = 0
_exec_count = 0

# Optional Prometheus instrumentation
_PROMETHEUS_AVAILABLE = False
try:
    import prometheus_client as prom

    # register some basic metrics
    _PROMETHEUS_AVAILABLE = True
    SCRIPT_DURATION = prom.Histogram(
        "econ_sim_script_duration_seconds",
        "Per-script execution duration (seconds)",
    )
    SCRIPT_EXECUTIONS = prom.Counter(
        "econ_sim_script_executions_total", "Total script executions"
    )
    SCRIPT_TIMEOUTS = prom.Counter(
        "econ_sim_script_timeouts_total", "Total script timeouts"
    )
    SCRIPT_SAMPLES = prom.Gauge("econ_sim_script_samples", "Sample window size")
except Exception:
    _PROMETHEUS_AVAILABLE = False


def _get_process_pool() -> concurrent.futures.ProcessPoolExecutor:
    global _PROCESS_POOL
    with _POOL_LOCK:
        if _PROCESS_POOL is not None:
            return _PROCESS_POOL
        cpu = os.cpu_count() or 2
        max_workers = max(2, min(8, cpu))
        _PROCESS_POOL = concurrent.futures.ProcessPoolExecutor(max_workers=max_workers)
        return _PROCESS_POOL


def get_sandbox_metrics() -> Dict[str, object]:
    """Return simple execution metrics for monitoring and alerts.

    Returns: dict with avg/p95 durations, total executions and timeouts.
    """
    with _metrics_lock:
        durations = list(_script_durations)
        total = _exec_count
        timeouts = _timeout_count
    if durations:
        avg = sum(durations) / len(durations)
        p95 = sorted(durations)[max(0, int(len(durations) * 0.95) - 1)]
    else:
        avg = 0.0
        p95 = 0.0
    return {
        "avg_sec": avg,
        "p95_sec": p95,
        "samples": len(durations),
        "total_executions": total,
        "timeouts": timeouts,
    }


def execute_script(
    code: str,
    context: Dict[str, Any],
    *,
    timeout: float = DEFAULT_SANDBOX_TIMEOUT,
    script_id: Optional[str] = None,
    allowed_modules: Optional[Iterable[str]] = None,
) -> Any:
    """Execute script in a reusable process pool and return the result.

    This implementation submits a callable to a ProcessPoolExecutor so worker
    processes are reused instead of spawning per-call. The call waits for up to
    `timeout` seconds and raises ScriptSandboxTimeout on timeout.
    """

    if timeout <= 0:
        raise ValueError("timeout must be positive")

    # Avoid an unconditional JSON round-trip which is expensive for large contexts.
    # If the context is already JSON-serializable, use it directly; otherwise
    # fall back to the json round-trip to coerce types to primitives.
    try:
        json.dumps(context)
        safe_context = context
    except (TypeError, ValueError):
        safe_context = json.loads(json.dumps(context))
    modules = (
        set(allowed_modules) if allowed_modules is not None else set(ALLOWED_MODULES)
    )

    pool = _get_process_pool()
    start = time.time()
    future = pool.submit(_pool_worker, code, safe_context, modules)
    success = False
    try:
        result = future.result(timeout=timeout)
        success = True
    except concurrent.futures.TimeoutError as exc:
        # Best-effort cancel and record metric
        try:
            future.cancel()
        except Exception:
            pass
        with _metrics_lock:
            global _timeout_count, _exec_count
            _timeout_count += 1
            _exec_count += 1
            if _PROMETHEUS_AVAILABLE:
                try:
                    SCRIPT_TIMEOUTS.inc()
                except Exception:
                    pass
        raise ScriptSandboxTimeout(
            f"脚本执行超时: {timeout} 秒" + (f" (id={script_id})" if script_id else "")
        ) from exc
    except Exception as exc:
        with _metrics_lock:
            _exec_count += 1
            if _PROMETHEUS_AVAILABLE:
                try:
                    SCRIPT_EXECUTIONS.inc()
                except Exception:
                    pass
        raise ScriptSandboxError(f"脚本执行失败: {exc}") from exc
    finally:
        elapsed = time.time() - start
        with _metrics_lock:
            _script_durations.append(elapsed)
            # count successful executions
            if success:
                _exec_count += 1
            if _PROMETHEUS_AVAILABLE:
                try:
                    SCRIPT_DURATION.observe(elapsed)
                    SCRIPT_SAMPLES.set(len(_script_durations))
                    if success:
                        SCRIPT_EXECUTIONS.inc()
                except Exception:
                    pass

    # result may be arbitrary; treat exceptions raised in worker as failures
    return result


def _pool_worker(code: str, context: Dict[str, Any], allowed_modules: Set[str]) -> Any:
    """Worker function executed inside pool worker process.

    This function is intentionally top-level so it can be pickled by the
    ProcessPoolExecutor.
    """
    try:
        _apply_resource_limits()
        safe_builtins = _build_safe_builtins(allowed_modules)
        sandbox_globals: Dict[str, Any] = {"__builtins__": safe_builtins}
        exec(code, sandbox_globals, sandbox_globals)
        func = sandbox_globals.get("generate_decisions")
        if func is None or not callable(func):
            raise ScriptSandboxError(
                "脚本中必须定义可调用的 generate_decisions(context) 函数"
            )
        return func(context)
    except Exception:
        # Re-raise to be captured by future.exception() in the parent
        raise


def _build_safe_builtins(allowed_modules: Set[str]) -> MappingProxyType:
    safe: Dict[str, Any] = {}
    for name in _ALLOWED_BUILTINS:
        if hasattr(builtins, name):
            safe[name] = getattr(builtins, name)
    safe["__build_class__"] = getattr(builtins, "__build_class__")

    original_import = builtins.__import__

    def safe_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
        if level != 0:
            raise ImportError("禁止相对导入")
        if not _module_allowed(name, allowed_modules):
            raise ImportError(f"模块 {name!r} 不在允许列表中")
        return original_import(name, globals, locals, fromlist, level)

    safe["__import__"] = safe_import
    return MappingProxyType(safe)


def _module_allowed(name: str, allowed: Set[str]) -> bool:
    return any(name == module or name.startswith(f"{module}.") for module in allowed)


def _apply_resource_limits() -> None:
    if resource is None:  # pragma: no cover - platform does not support
        return
    try:
        resource.setrlimit(
            resource.RLIMIT_CPU, (CPU_TIME_LIMIT_SECONDS, CPU_TIME_LIMIT_SECONDS)
        )
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    except (ValueError, OSError):
        pass
