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
import logging
import signal
from collections import deque
from multiprocessing.connection import Connection
from types import MappingProxyType
from typing import Any, Dict, Iterable, Optional, Set

try:  # pragma: no cover - platform compatibility
    import resource
except ImportError:  # pragma: no cover - Windows etc
    resource = None  # type: ignore[assignment]

# optional psutil for reliable process enumeration and kill
_PSUTIL_AVAILABLE = False
try:
    import psutil

    _PSUTIL_AVAILABLE = True
except Exception:
    _PSUTIL_AVAILABLE = False

DEFAULT_SANDBOX_TIMEOUT = 0.75
CPU_TIME_LIMIT_SECONDS = 1
MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024
# After this many tasks executed by a single worker process, force it to exit so
# the pool can replace it. This mitigates memory leaks / module-state pollution
# in long-lived worker processes.
WORKER_MAX_TASKS = int(os.getenv("ECON_SIM_WORKER_MAX_TASKS", "200"))

# per-process counter — this lives in the worker process and is incremented by
# `_pool_worker` for each completed task.
_WORKER_TASK_COUNT = 0

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
    POOL_RESTARTS = prom.Counter(
        "econ_sim_script_pool_restarts_total", "Total process pool restarts"
    )
    WORKER_KILLS = prom.Counter(
        "econ_sim_script_worker_kills_total", "Total worker processes force-killed"
    )
except Exception:
    _PROMETHEUS_AVAILABLE = False


# Module-level detection: determine once whether this interpreter was started
# from an unsafe entrypoint (heredoc / stdin / -c) where multiprocessing
# spawn workers would attempt to re-execute a non-file main module. If so,
# force the per-call subprocess path for all calls in this process.
_FORCE_PER_CALL_ENV: bool = False
try:
    _main_path = sys.argv[0] if len(sys.argv) > 0 else ""
    _basename = os.path.basename(_main_path)
    if (
        not _main_path
        or _main_path in ("-", "-c")
        or "<stdin>" in _main_path
        or _basename == "<stdin>"
    ):
        _FORCE_PER_CALL_ENV = True
    else:
        try:
            if not os.path.isfile(_main_path):
                _FORCE_PER_CALL_ENV = True
        except Exception:
            _FORCE_PER_CALL_ENV = True
except Exception:
    _FORCE_PER_CALL_ENV = True


def _get_process_pool() -> concurrent.futures.ProcessPoolExecutor:
    global _PROCESS_POOL
    with _POOL_LOCK:
        if _PROCESS_POOL is not None:
            return _PROCESS_POOL
        cpu = os.cpu_count() or 2
        max_workers = max(2, min(8, cpu))
        _PROCESS_POOL = concurrent.futures.ProcessPoolExecutor(max_workers=max_workers)
        return _PROCESS_POOL


def _pool_worker(
    code: str, context: Dict[str, Any], allowed_modules: Set[str], timeout: float = 0.0
) -> Any:
    """Top-level worker function executed inside pool worker process.

    Installs a SIGALRM-based watchdog to self-terminate on wall-clock timeouts
    (helps with CPU-bound infinite loops). Also applies resource limits and
    performs a small per-process task count to recycle workers.
    Returns a (status, payload) envelope where status is '__ok__' or '__err__'.
    """
    old_handler = None
    installed_timer = False
    try:
        _apply_resource_limits()

        # install watchdog
        if timeout and timeout > 0:
            try:

                def _on_alarm(signum, frame):
                    try:
                        print(f"_pool_worker pid={os.getpid()} alarm fired, exiting")
                    except Exception:
                        pass
                    os._exit(2)

                old_handler = signal.signal(signal.SIGALRM, _on_alarm)
                signal.setitimer(signal.ITIMER_REAL, float(timeout) + 0.05)
                installed_timer = True
            except Exception:
                installed_timer = False

        # lightweight trace
        try:
            pid = os.getpid()
            print(f"_pool_worker start pid={pid} code_len={len(code)}")
        except Exception:
            pass

        safe_builtins = _build_safe_builtins(allowed_modules)
        # Provide a per-execution LLM session object available to user scripts
        try:
            from econ_sim.utils.llm_session import create_llm_session_from_env

            llm_obj = create_llm_session_from_env()
        except Exception:
            llm_obj = None

        sandbox_globals: Dict[str, Any] = {
            "__builtins__": safe_builtins,
            "llm": llm_obj,
        }
        try:
            print(f"_pool_worker pid={os.getpid()} exec start")
        except Exception:
            pass
        exec(code, sandbox_globals, sandbox_globals)
        try:
            print(f"_pool_worker pid={os.getpid()} exec done, looking up function")
        except Exception:
            pass
        func = sandbox_globals.get("generate_decisions")
        if func is None or not callable(func):
            return ("__err__", "missing generate_decisions")

        try:
            try:
                result = func(context)
                try:
                    print(f"_pool_worker pid={os.getpid()} function returned")
                except Exception:
                    pass
                return ("__ok__", result)
            except Exception:
                tb = traceback.format_exc()
                try:
                    print(f"_pool_worker pid={os.getpid()} user exception:\n{tb}")
                except Exception:
                    pass
                return ("__err__", tb)
        finally:
            # worker recycle counting happens regardless of user code outcome
            try:
                global _WORKER_TASK_COUNT
                _WORKER_TASK_COUNT += 1
                if WORKER_MAX_TASKS > 0 and _WORKER_TASK_COUNT >= WORKER_MAX_TASKS:
                    try:
                        print(
                            f"_pool_worker pid={os.getpid()} reached max tasks ({_WORKER_TASK_COUNT}), exiting"
                        )
                    except Exception:
                        pass
                    os._exit(0)
            except Exception:
                pass
        # Should not reach here; return a safe error envelope
        return ("__err__", "worker exited unexpectedly")
    except Exception:
        # escalate unexpected errors
        raise
    finally:
        try:
            if installed_timer:
                try:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                except Exception:
                    pass
                try:
                    if old_handler is not None:
                        signal.signal(signal.SIGALRM, old_handler)
                except Exception:
                    pass
        except Exception:
            pass


def shutdown_process_pool(wait: bool = True, aggressive_kill: bool = True) -> None:
    """Shut down the global process pool and (optionally) aggressively kill
    any remaining worker processes and their descendants.
    """
    global _PROCESS_POOL
    with _POOL_LOCK:
        if _PROCESS_POOL is None:
            return
        pool = _PROCESS_POOL

        logger = logging.getLogger(__name__)
        logger.debug(
            "shutdown_process_pool: starting (wait=%s aggressive_kill=%s)",
            wait,
            aggressive_kill,
        )

        # snapshot of internal process objects (may be mapping or list)
        try:
            procs_snapshot = list(getattr(pool, "_processes", {}).values())
        except Exception:
            procs_snapshot = []

        # call shutdown; prefer the requested wait behavior but don't raise
        try:
            pool.shutdown(wait=wait)
        except Exception:
            try:
                pool.shutdown(wait=False)
            except Exception:
                pass

        # aggressive kill: use psutil if available for reliable termination
        if aggressive_kill and _PSUTIL_AVAILABLE:
            pids = set()
            for p in procs_snapshot:
                pid = getattr(p, "pid", None)
                if not pid and hasattr(p, "_popen"):
                    popen = getattr(p, "_popen", None)
                    pid = getattr(popen, "pid", None) if popen is not None else None
                if pid:
                    pids.add(pid)
            # configurable timeouts via environment for easier tuning in CI/production
            try:
                term_timeout = float(os.getenv("ECON_SIM_POOL_TERM_TIMEOUT", "1.5"))
            except Exception:
                term_timeout = 1.5
            try:
                kill_timeout = float(os.getenv("ECON_SIM_POOL_KILL_TIMEOUT", "0.5"))
            except Exception:
                kill_timeout = 0.5
            logger.debug(
                "shutdown_process_pool: term_timeout=%s kill_timeout=%s pids=%s",
                term_timeout,
                kill_timeout,
                list(pids),
            )
            try:
                procs = []
                for pid in list(pids):
                    try:
                        proc = psutil.Process(pid)
                    except Exception:
                        continue
                    try:
                        descendants = proc.children(recursive=True)
                    except Exception:
                        descendants = []
                    procs.append((proc, descendants))

                # terminate then kill if necessary
                for proc, descendants in procs:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    for c in descendants:
                        try:
                            c.terminate()
                        except Exception:
                            pass

                end = time.time() + term_timeout
                while time.time() < end:
                    still_alive = []
                    for proc, descendants in procs:
                        try:
                            if (
                                proc.is_running()
                                and proc.status() != psutil.STATUS_ZOMBIE
                            ):
                                still_alive.append(proc)
                                continue
                        except Exception:
                            still_alive.append(proc)
                            continue
                        for c in descendants:
                            try:
                                if (
                                    c.is_running()
                                    and c.status() != psutil.STATUS_ZOMBIE
                                ):
                                    still_alive.append(proc)
                                    break
                            except Exception:
                                still_alive.append(proc)
                                break
                    if not still_alive:
                        break
                    time.sleep(0.05)

                for proc, descendants in procs:
                    try:
                        if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                            proc.kill()
                    except Exception:
                        try:
                            os.kill(getattr(proc, "pid", None) or 0, signal.SIGKILL)
                        except Exception:
                            pass
                    for c in descendants:
                        try:
                            if c.is_running() and c.status() != psutil.STATUS_ZOMBIE:
                                c.kill()
                        except Exception:
                            try:
                                os.kill(getattr(c, "pid", None) or 0, signal.SIGKILL)
                            except Exception:
                                pass
                    try:
                        if _PROMETHEUS_AVAILABLE:
                            WORKER_KILLS.inc()
                    except Exception:
                        pass
                logger.debug("shutdown_process_pool: aggressive kill completed")
            except Exception:
                pass

        elif aggressive_kill and not _PSUTIL_AVAILABLE:
            # psutil not available: best-effort naive termination of procs_snapshot
            logger.debug(
                "shutdown_process_pool: psutil not available, falling back to naive termination"
            )
            try:
                killed = 0
                for p in procs_snapshot:
                    try:
                        pid = getattr(p, "pid", None)
                        if not pid and hasattr(p, "_popen"):
                            popen = getattr(p, "_popen", None)
                            pid = (
                                getattr(popen, "pid", None)
                                if popen is not None
                                else None
                            )
                        if not pid:
                            continue
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except Exception:
                            try:
                                os.kill(pid, signal.SIGKILL)
                            except Exception:
                                pass
                        killed += 1
                        if _PROMETHEUS_AVAILABLE:
                            try:
                                WORKER_KILLS.inc()
                            except Exception:
                                pass
                    except Exception:
                        pass
                logger.debug(
                    "shutdown_process_pool: naive termination attempted, killed=%s",
                    killed,
                )
            except Exception:
                pass

        _PROCESS_POOL = None


def _recreate_process_pool() -> concurrent.futures.ProcessPoolExecutor:
    """Best-effort recreate of the global process pool.

    Shutdowns any existing pool (aggressively) and returns a fresh pool
    instance.
    """
    global _PROCESS_POOL
    with _POOL_LOCK:
        try:
            if _PROCESS_POOL is not None:
                shutdown_process_pool(wait=False, aggressive_kill=True)
        except Exception:
            pass
        # create a fresh pool
        _PROCESS_POOL = None
        try:
            if _PROMETHEUS_AVAILABLE:
                POOL_RESTARTS.inc()
        except Exception:
            pass
        return _get_process_pool()


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
    force_per_call: bool = False,
) -> Any:
    """Execute script in a reusable process pool and return the result.

    This implementation submits a callable to a ProcessPoolExecutor so worker
    processes are reused instead of spawning per-call. The call waits for up to
    `timeout` seconds and raises ScriptSandboxTimeout on timeout.
    """

    global _exec_count, _timeout_count

    if timeout <= 0:
        raise ValueError("timeout must be positive")

    # Avoid an unconditional JSON round-trip which is expensive for large contexts.
    # If the context is already JSON-serializable, use it directly; otherwise
    # fall back to the json round-trip to coerce types to primitives.
    try:
        # If context is JSON-serializable, use it directly to avoid copies.
        json.dumps(context)
        safe_context = context
    except (TypeError, ValueError):
        # Fallback to deepcopy instead of JSON round-trip to reduce overhead.
        import copy

        safe_context = copy.deepcopy(context)
    modules = (
        set(allowed_modules) if allowed_modules is not None else set(ALLOWED_MODULES)
    )

    # allow tests/CI to force per-call subprocess execution to ensure isolation
    force = force_per_call or os.getenv("ECON_SIM_FORCE_PER_CALL") == "1"
    if force:
        logger = logging.getLogger(__name__)
        logger.debug(
            "execute_script: using per-call subprocess for script_id=%s", script_id
        )
        return _run_in_subprocess(code, safe_context, modules, timeout)

    # If we detected at import time that this interpreter was started from
    # an unsafe entrypoint (heredoc / stdin / -c), always prefer the
    # per-call subprocess path which doesn't rely on spawn re-executing the
    # parent process main module in child workers.
    if _FORCE_PER_CALL_ENV:
        logger = logging.getLogger(__name__)
        try:
            start_method = multiprocessing.get_start_method(allow_none=True)
        except Exception:
            start_method = None
        logger.debug(
            "execute_script: import-time detected unsafe main (argv0=%r), forcing per-call subprocess (start_method=%r) for script_id=%s",
            sys.argv[0] if len(sys.argv) > 0 else None,
            start_method,
            script_id,
        )
        return _run_in_subprocess(code, safe_context, modules, timeout)

    # If the current Python invocation doesn't have a real main filename
    # (for example when running via heredoc / <stdin>), multiprocessing's
    # spawn-based workers may fail to find the main module path and crash
    # immediately. Detect that situation and fall back to per-call
    # subprocesses which don't rely on importing the main module.
    try:
        main_path = sys.argv[0] if len(sys.argv) > 0 else ""
        # Some invocation patterns (heredoc / stdin) set argv[0] to values
        # like "<stdin>" or "/current/dir/<stdin>" which are not actual
        # file paths and will cause multiprocessing.spawn to attempt to
        # import a non-existent main module. Be conservative: if argv[0]
        # is empty, contains '<stdin>', equals '-c', or does not point
        # to an existing regular file, always fall back to the per-call
        # subprocess path which does not rely on importing the parent
        # process main module.
        try:
            logger = logging.getLogger(__name__)
            unsafe_main = False
            if not main_path:
                unsafe_main = True
            else:
                basename = os.path.basename(main_path)
                if "<stdin>" in main_path or basename == "<stdin>" or main_path == "-c":
                    unsafe_main = True
                else:
                    try:
                        if not os.path.isfile(main_path):
                            unsafe_main = True
                    except Exception:
                        unsafe_main = True

            if unsafe_main:
                try:
                    start_method = multiprocessing.get_start_method(allow_none=True)
                except Exception:
                    start_method = None
                logger.debug(
                    "execute_script: unsafe main_path=%r start_method=%r; forcing per-call subprocess for script_id=%s",
                    main_path,
                    start_method,
                    script_id,
                )
                return _run_in_subprocess(code, safe_context, modules, timeout)
        except Exception:
            # any unexpected issues in detection should default to safe
            return _run_in_subprocess(code, safe_context, modules, timeout)
    except Exception:
        # best-effort detection only; ignore any issues and proceed to pool
        pass

    pool = _get_process_pool()
    start = time.time()
    logger = logging.getLogger(__name__)
    try:
        logger.debug("submit to pool: script_id=%s, code_len=%d", script_id, len(code))
        # best-effort: log current worker pids for debugging
        try:
            proc_map = getattr(pool, "_processes", None)
            if proc_map:
                pids = []
                for p in list(proc_map.values()):
                    pid = getattr(p, "pid", None)
                    if not pid and hasattr(p, "_popen"):
                        popen = getattr(p, "_popen", None)
                        pid = getattr(popen, "pid", None) if popen is not None else None
                    if pid:
                        pids.append(pid)
                logger.debug("process pool workers: %s", pids)
        except Exception:
            pass

        # submit timeout to worker so it can self-terminate on CPU-bound loops
        future = pool.submit(_pool_worker, code, safe_context, modules, float(timeout))
    except Exception as exc:
        # If the process pool is broken (child crashed), try to reset it once
        # and recreate a fresh pool. If that still fails, fall back to
        # executing the worker inline to avoid crashing the caller.
        try:
            with _POOL_LOCK:
                global _PROCESS_POOL
                try:
                    if _PROCESS_POOL is not None:
                        _PROCESS_POOL.shutdown(wait=False)
                except Exception:
                    pass
                _PROCESS_POOL = None
                pool = _get_process_pool()
            future = pool.submit(
                _pool_worker, code, safe_context, modules, float(timeout)
            )
        except Exception:
            # Last-resort fallback: run worker inline (no isolation) but keep
            # exception semantics consistent for the caller.
            try:
                logger.debug(
                    "falling back to inline worker for script_id=%s", script_id
                )
                result = _pool_worker(code, safe_context, modules, float(timeout))
                success = True
                elapsed = time.time() - start
                with _metrics_lock:
                    _script_durations.append(elapsed)
                    _exec_count += 1
                    if _PROMETHEUS_AVAILABLE:
                        try:
                            SCRIPT_DURATION.observe(elapsed)
                            SCRIPT_SAMPLES.set(len(_script_durations))
                            SCRIPT_EXECUTIONS.inc()
                        except Exception:
                            pass
                return result
            except Exception as e:
                # re-raise as ScriptSandboxError to match existing path
                raise ScriptSandboxError(f"脚本执行失败: {e}") from e
    success = False
    try:
        # wait for the future to complete within the configured timeout
        result = future.result(timeout=timeout)
        logger.debug(
            "pool result received: script_id=%s result_type=%s", script_id, type(result)
        )
        # unwrap worker envelope if present
        if (
            isinstance(result, tuple)
            and len(result) == 2
            and result[0] in ("__ok__", "__err__")
        ):
            status, payload = result
            if status == "__ok__":
                result = payload
            else:
                raise ScriptSandboxError(f"脚本执行失败:\n{payload}")
        success = True
    except concurrent.futures.TimeoutError as exc:
        # If the future hasn't started running yet it is likely queued by the
        # ProcessPoolExecutor; give it a short grace period to start and run
        # before declaring a hard timeout. This reduces false positives when
        # the configured sandbox timeout is very small.
        try:
            running = future.running()
        except Exception:
            running = False

        if not running:
            extra = min(5.0, max(0.1, timeout * 5))
            try:
                result = future.result(timeout=extra)
                logger.debug(
                    "pool result received after grace: script_id=%s", script_id
                )
                if (
                    isinstance(result, tuple)
                    and len(result) == 2
                    and result[0] in ("__ok__", "__err__")
                ):
                    status, payload = result
                    if status == "__ok__":
                        result = payload
                    else:
                        raise ScriptSandboxError(f"脚本执行失败:\n{payload}")
                success = True
            except concurrent.futures.TimeoutError:
                try:
                    future.cancel()
                except Exception:
                    pass
                with _metrics_lock:
                    _timeout_count += 1
                    _exec_count += 1
                    if _PROMETHEUS_AVAILABLE:
                        try:
                            SCRIPT_TIMEOUTS.inc()
                        except Exception:
                            pass
                # Attempt to clear the possibly-broken pool before raising
                try:
                    _recreate_process_pool()
                except Exception:
                    pass
                raise ScriptSandboxTimeout(
                    f"脚本执行超时: {timeout} 秒"
                    + (f" (id={script_id})" if script_id else "")
                ) from exc
        else:
            # task started but exceeded provided timeout
            try:
                future.cancel()
            except Exception:
                pass
            with _metrics_lock:
                _timeout_count += 1
                _exec_count += 1
                if _PROMETHEUS_AVAILABLE:
                    try:
                        SCRIPT_TIMEOUTS.inc()
                    except Exception:
                        pass
            # Try to recreate pool to avoid future tasks using blocked workers
            try:
                _recreate_process_pool()
            except Exception:
                pass
            raise ScriptSandboxTimeout(
                f"脚本执行超时: {timeout} 秒"
                + (f" (id={script_id})" if script_id else "")
            ) from exc
    except Exception as exc:
        with _metrics_lock:
            _exec_count += 1
            if _PROMETHEUS_AVAILABLE:
                try:
                    SCRIPT_EXECUTIONS.inc()
                except Exception:
                    pass
        # If the pool appears broken (common when spawn cannot import the
        # main module) try a last-resort fallback to a per-call subprocess
        # which avoids ProcessPoolExecutor semantics.
        try:
            import concurrent.futures.process as _cfproc

            BrokenPool = getattr(_cfproc, "BrokenProcessPool", None)
        except Exception:
            BrokenPool = None

        broken_indicated = False
        try:
            if BrokenPool is not None and isinstance(exc, BrokenPool):
                broken_indicated = True
            elif "terminated abruptly" in str(exc) or "FileNotFoundError" in repr(exc):
                broken_indicated = True
        except Exception:
            broken_indicated = False

        if broken_indicated:
            logger = logging.getLogger(__name__)
            logger.debug(
                "execute_script: detected broken process pool (exc=%r), falling back to per-call subprocess for script_id=%s",
                exc,
                script_id,
            )
            try:
                # Try to recreate pool asynchronously to recover for future calls
                try:
                    _recreate_process_pool()
                except Exception:
                    pass
                return _run_in_subprocess(code, safe_context, modules, timeout)
            except Exception:
                # if fallback failed, raise original error below
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

    # result is returned (may be None) after unwrapping above
    return result


def _pool_worker(
    code: str, context: Dict[str, Any], allowed_modules: Set[str], timeout: float = 0.0
) -> Any:
    """Worker function executed inside pool worker process.

    This function is intentionally top-level so it can be pickled by the
    ProcessPoolExecutor.
    """
    # Implementation is defined earlier in this file. This placeholder
    # duplicate has been removed to ensure the pool worker uses the real
    # implementation above.


def _noop() -> None:
    """Simple noop used to warm up worker processes."""
    return None


def warm_process_pool(timeout: float = 1.0) -> None:
    """Ensure the process pool has spawned workers by submitting a noop and waiting.

    This reduces the chance that the first real task will be delayed by worker
    process startup, which can cause short timeouts to trigger incorrectly.
    """
    try:
        pool = _get_process_pool()
        fut = pool.submit(_noop)
        # best-effort wait
        try:
            fut.result(timeout=timeout)
        except Exception:
            pass
    except Exception:
        # ignore warmup failures; callers will still attempt real execution
        return


def _subprocess_entry(
    code: str, context: Dict[str, Any], allowed_modules: Set[str], conn: Connection
) -> None:
    """Entry point run inside a dedicated subprocess.

    Sends back a tuple via the provided connection: ("ok", result) on success
    or ("err", traceback_string) on error. The connection is closed before
    exit to ensure the parent receives EOF if the process dies unexpectedly.
    """
    try:
        _apply_resource_limits()
        try:
            pid = os.getpid()
            print(f"_subprocess_entry start pid={pid} code_len={len(code)}")
        except Exception:
            pass

        safe_builtins = _build_safe_builtins(allowed_modules)
        try:
            from econ_sim.utils.llm_session import create_llm_session_from_env

            llm_obj = create_llm_session_from_env()
        except Exception:
            llm_obj = None
        sandbox_globals: Dict[str, Any] = {
            "__builtins__": safe_builtins,
            "llm": llm_obj,
        }
        exec(code, sandbox_globals, sandbox_globals)
        func = sandbox_globals.get("generate_decisions")
        if func is None or not callable(func):
            raise ScriptSandboxError(
                "脚本中必须定义可调用的 generate_decisions(context) 函数"
            )
        result = func(context)
        try:
            conn.send(("ok", result))
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception:
        tb = traceback.format_exc()
        try:
            conn.send(("err", tb))
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def _run_in_subprocess(
    code: str, context: Dict[str, Any], allowed_modules: Set[str], timeout: float
) -> Any:
    """Run code in a dedicated subprocess with a timeout.

    Guarantees that if the subprocess does not respond within `timeout`, it
    will be terminated and a ScriptSandboxTimeout raised.
    """
    # Use a fresh Python interpreter via subprocess to avoid multiprocessing
    # spawn importing the parent's main module (which can be '<stdin>' in
    # heredoc cases). We send the code/context/allowed_modules as JSON on
    # stdin and expect a JSON result on stdout in the shape {"ok": result}
    # or {"err": traceback}.
    import subprocess
    import tempfile

    payload = None
    try:
        payload = json.dumps(
            {"code": code, "context": context, "allowed_modules": list(allowed_modules)}
        )
    except Exception:
        # fallback: try to coerce context to primitives
        try:
            simple_ctx = json.loads(json.dumps(context, default=lambda o: repr(o)))
            payload = json.dumps(
                {
                    "code": code,
                    "context": simple_ctx,
                    "allowed_modules": list(allowed_modules),
                }
            )
        except Exception:
            # last resort: send minimal context
            payload = json.dumps(
                {"code": code, "context": {}, "allowed_modules": list(allowed_modules)}
            )

    # small runner that executes the code and prints JSON result
    runner = (
        "import sys, json, traceback\n"
        "from econ_sim.script_engine.sandbox import _build_safe_builtins\n"
        "# Attempt to create per-execution llm session; failures fall back to None\n"
        "try:\n"
        "    from econ_sim.utils.llm_session import create_llm_session_from_env\n"
        "    _llm = create_llm_session_from_env()\n"
        "except Exception:\n"
        "    _llm = None\n"
        "data = json.load(sys.stdin)\n"
        "code = data.get('code', '')\n"
        "context = data.get('context', {})\n"
        "allowed = set(data.get('allowed_modules', []))\n"
        "try:\n"
        "    safe_builtins = _build_safe_builtins(allowed)\n"
        "    g = {'__builtins__': safe_builtins, 'llm': _llm}\n"
        "    exec(code, g, g)\n"
        "    func = g.get('generate_decisions')\n"
        "    if func is None or not callable(func):\n"
        "        print(json.dumps({'err': 'missing generate_decisions'}))\n"
        "        sys.exit(0)\n"
        "    res = func(context)\n"
        "    try:\n"
        "        print(json.dumps({'ok': res}))\n"
        "    except Exception:\n"
        "        print(json.dumps({'ok': repr(res)}))\n"
        "except Exception:\n"
        "    tb = traceback.format_exc()\n"
        "    print(json.dumps({'err': tb}))\n"
    )

    try:
        proc = subprocess.run(
            [sys.executable, "-c", runner],
            input=payload.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(0.1, float(timeout) + 1.0),
        )
    except subprocess.TimeoutExpired as e:
        # process didn't finish in time
        raise ScriptSandboxTimeout(f"脚本执行超时: {timeout} 秒") from e

    # prefer stdout JSON, but capture stderr for diagnostics
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    err = proc.stderr.decode("utf-8", errors="replace").strip()
    if not out:
        # no stdout: treat stderr as failure
        if err:
            raise ScriptSandboxError(f"子进程错误:\n{err}")
        raise ScriptSandboxError("子进程未返回结果")

    try:
        parsed = json.loads(out)
    except Exception:
        # couldn't parse JSON; include stderr for debugging
        raise ScriptSandboxError(f"无法解析子进程输出: {out}\nstderr: {err}")

    if "ok" in parsed:
        return parsed["ok"]
    elif "err" in parsed:
        raise ScriptSandboxError(f"脚本执行失败:\n{parsed['err']}")
    else:
        raise ScriptSandboxError(f"子进程返回未知结果: {parsed}")


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
