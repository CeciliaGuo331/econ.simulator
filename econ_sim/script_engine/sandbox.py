"""脚本沙箱与执行器：为用户上传的策略脚本提供受控、可监控的执行环境。

功能与设计要点：
- 支持两种执行模式：
    1. 进程池模式（ProcessPoolExecutor）：重用工作进程以降低反复创建子进程的开销；
    2. 每次调用子进程模式（per-call subprocess）：在某些不安全的启动环境（如 heredoc 或 -c）或测试场景中使用，避免 spawn 导入主模块的问题。
- 在工作进程中安装时间监控与资源限制（如 SIGALRM watchdog、CPU/内存 rlimit），
    以防止脚本的无限循环或资源耗尽影响主进程。
- 提供安全的执行沙箱：
    - 只允许有限的内置函数与白名单模块导入；
    - 使用受控的内置字典与自定义 `__import__` 函数来阻止未授权模块导入；
    - 在沙箱中注入 `llm` 对象（若可用）供脚本在受限范围内调用。
- 提供度量与自愈机制：
    - 统计脚本执行延迟、超时次数与执行次数；
    - 当进程池损坏或工作进程异常退出时，尝试重建进程池或回退到 per-call subprocess。
- 安全与可配置点：
    - `ALLOWED_MODULES`、`_ALLOWED_BUILTINS`：控制脚本可使用的模块与内置函数；
    - `DEFAULT_SANDBOX_TIMEOUT`、`WORKER_MAX_TASKS` 等均可通过环境变量调优；
    - `ECON_SIM_FORCE_PER_CALL` 环境变量可强制使用 per-call subprocess，以便在 CI/特殊环境中提高隔离性。

推荐做法：将复杂或需要外部网络访问的逻辑移出用户脚本，
通过受控的 provider（如项目提供的 LLM 适配器）以最小化安全风险。
"""

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
import importlib

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

DEFAULT_SANDBOX_TIMEOUT = 120
CPU_TIME_LIMIT_SECONDS = 120
MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024
# 当单个工作进程执行到达此任务数量时，强制其退出以便进程池替换它。
# 这有助于缓解长期运行工作进程中的内存泄漏或模块状态污染问题。
WORKER_MAX_TASKS = int(os.getenv("ECON_SIM_WORKER_MAX_TASKS", "200"))

# per-process counter — this lives in the worker process and is incremented by
# `_pool_worker` for each completed task.
_WORKER_TASK_COUNT = 0

ALLOWED_MODULES: Set[str] = {
    "math",
    "statistics",
    "random",
    "time",
    "re",
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
    "getattr",
    "hasattr",
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
        # 根据可用 CPU 数量确定池大小（在 2 到 8 之间），避免在资源受限环境中创建过多子进程。
        cpu = os.cpu_count() or 2
        max_workers = max(2, min(8, cpu))
        _PROCESS_POOL = concurrent.futures.ProcessPoolExecutor(max_workers=max_workers)
        # best-effort debug info for pool creation
        try:
            logger = logging.getLogger(__name__)
            logger.info("created process pool (max_workers=%s)", max_workers)
            print(f"_get_process_pool: created pool max_workers={max_workers}")
        except Exception:
            pass
        return _PROCESS_POOL


def _pool_worker(
    code: str,
    context: Dict[str, Any],
    allowed_modules: Set[str],
    timeout: float = 0.0,
    llm_factory_path: Optional[str] = None,
    llm_session: Optional[Any] = None,
) -> Any:
    """在池中工作进程内执行的顶层 worker 函数。

    行为说明：
    - 安装基于 SIGALRM 的看门狗用于在真实时间超时时自我终止（对 CPU 密集型无限循环有帮助）；
    - 应用资源限制（CPU/内存）；
    - 使用每进程任务计数，当达到阈值时回收工作进程以减少内存污染。
    返回一个 (status, payload) 的封包，status 为 '__ok__' 或 '__err__'。
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

        # LLM 注入优先级：显式传入的实例 -> 工厂路径（在 worker 进程内 import 并调用）
        # 如果两者都不存在或工厂创建失败，则不再回退到基于环境的默认构造（避免全局耦合）。
        llm_obj = None
        try:
            if llm_session is not None:
                llm_obj = llm_session
            elif llm_factory_path:
                try:
                    mod_name, attr = llm_factory_path.rsplit(".", 1)
                    mod = importlib.import_module(mod_name)
                    factory = getattr(mod, attr)
                    llm_obj = factory()
                except Exception:
                    llm_obj = None
            else:
                # explicitly do NOT construct from environment here
                llm_obj = None
        except Exception:
            llm_obj = None

        # Wrap injected llm object with a logging proxy so the system (not user
        # scripts) can record when LLM calls occur and which system model is used.
        try:
            if llm_obj is not None:
                import asyncio
                import inspect

                logger = logging.getLogger("econ_sim.llm")

                class _LLMLoggingProxy:
                    def __init__(self, real):
                        self._real = real
                        # try common places for system_model
                        self.system_model = getattr(
                            real, "system_model", None
                        ) or getattr(
                            getattr(real, "provider", None), "system_model", None
                        )

                        # Control detailed logging of prompts/outputs via env var.
                        # Set ECON_SIM_LLM_LOG_FULL=1 to enable logging full prompt
                        # and full response content + usage_tokens at DEBUG level.
                        try:
                            self._log_full = (
                                os.getenv("ECON_SIM_LLM_LOG_FULL", "0") == "1"
                            )
                        except Exception:
                            self._log_full = False

                    def _unwrap_content(self, resp):
                        if resp is None:
                            return None
                        if isinstance(resp, dict):
                            return resp.get("content")
                        return getattr(resp, "content", str(resp))

                    def generate(self, *args, **kwargs):
                        try:
                            user_id = kwargs.get("user_id")
                            prompt = args[0] if args else kwargs.get("prompt")
                            plen = len(prompt) if isinstance(prompt, str) else 0
                            logger.info(
                                "llm.generate called: system_model=%s user_id=%s prompt_len=%s",
                                self.system_model,
                                user_id,
                                plen,
                            )
                            # debug: log prompt preview and optionally full prompt
                            try:
                                if self._log_full:
                                    logger.debug(
                                        "llm.generate prompt (full)=%r", prompt
                                    )
                                else:
                                    preview_p = (
                                        (prompt[:400] + "...")
                                        if prompt and len(prompt) > 400
                                        else prompt
                                    )
                                    logger.debug(
                                        "llm.generate prompt_preview=%r", preview_p
                                    )
                            except Exception:
                                pass
                        except Exception:
                            pass

                        try:
                            result = self._real.generate(*args, **kwargs)
                        except Exception:
                            logger.exception("llm.generate raised")
                            raise

                        # support if generate returned a coroutine
                        try:
                            if asyncio.iscoroutine(result):
                                try:
                                    result = asyncio.run(result)
                                except RuntimeError:
                                    loop = asyncio.new_event_loop()
                                    try:
                                        result = loop.run_until_complete(
                                            self._real.generate(*args, **kwargs)
                                        )
                                    finally:
                                        try:
                                            loop.close()
                                        except Exception:
                                            pass
                        except Exception:
                            pass

                        try:
                            content = self._unwrap_content(result)
                            # Attempt to extract usage tokens if available
                            usage = None
                            try:
                                usage = int(getattr(result, "usage_tokens", None) or 0)
                            except Exception:
                                try:
                                    usage = (
                                        int(result.get("usage_tokens", 0))
                                        if isinstance(result, dict)
                                        else None
                                    )
                                except Exception:
                                    usage = None

                            if self._log_full:
                                logger.debug(
                                    "llm.generate returned: system_model=%s content=%r usage_tokens=%r",
                                    self.system_model,
                                    content,
                                    usage,
                                )
                            else:
                                preview = (
                                    (content[:200] + "...")
                                    if content and len(content) > 200
                                    else content
                                )
                                logger.info(
                                    "llm.generate returned: system_model=%s preview=%r usage_tokens=%r",
                                    self.system_model,
                                    preview,
                                    usage,
                                )
                        except Exception:
                            pass
                        return result

                    async def complete(self, *args, **kwargs):
                        try:
                            user_id = kwargs.get("user_id")
                            prompt = args[0] if args else kwargs.get("prompt")
                            plen = len(prompt) if isinstance(prompt, str) else 0
                            logger.info(
                                "llm.complete called: system_model=%s user_id=%s prompt_len=%s",
                                self.system_model,
                                user_id,
                                plen,
                            )
                            try:
                                if self._log_full:
                                    logger.debug(
                                        "llm.complete prompt (full)=%r", prompt
                                    )
                                else:
                                    preview_p = (
                                        (prompt[:400] + "...")
                                        if prompt and len(prompt) > 400
                                        else prompt
                                    )
                                    logger.debug(
                                        "llm.complete prompt_preview=%r", preview_p
                                    )
                            except Exception:
                                pass
                        except Exception:
                            pass

                        try:
                            result = self._real.complete(*args, **kwargs)
                        except Exception:
                            logger.exception("llm.complete raised")
                            raise

                        # if complete returned coroutine-like, await it
                        try:
                            if inspect.isawaitable(result):
                                result = await result
                        except Exception:
                            # fallback: try running in new loop
                            try:
                                loop = asyncio.new_event_loop()
                                try:
                                    result = loop.run_until_complete(
                                        self._real.complete(*args, **kwargs)
                                    )
                                finally:
                                    try:
                                        loop.close()
                                    except Exception:
                                        pass
                            except Exception:
                                pass

                        try:
                            content = self._unwrap_content(result)
                            # Attempt to extract usage tokens if available
                            usage = None
                            try:
                                usage = int(getattr(result, "usage_tokens", None) or 0)
                            except Exception:
                                try:
                                    usage = (
                                        int(result.get("usage_tokens", 0))
                                        if isinstance(result, dict)
                                        else None
                                    )
                                except Exception:
                                    usage = None

                            if self._log_full:
                                logger.debug(
                                    "llm.complete returned: system_model=%s content=%r usage_tokens=%r",
                                    self.system_model,
                                    content,
                                    usage,
                                )
                            else:
                                preview = (
                                    (content[:200] + "...")
                                    if content and len(content) > 200
                                    else content
                                )
                                logger.info(
                                    "llm.complete returned: system_model=%s preview=%r usage_tokens=%r",
                                    self.system_model,
                                    preview,
                                    usage,
                                )
                        except Exception:
                            pass
                        return result

                try:
                    llm_obj = _LLMLoggingProxy(llm_obj)
                except Exception:
                    pass
        except Exception:
            pass

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
        # 从沙箱全局命名空间中查找 generate_decisions 函数并调用它
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
            # 无论用户代码结果如何，均进行工作进程回收计数
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
    """关闭全局进程池，并可选择强制终止剩余的工作进程及其子进程。

    参数说明：
    - wait: 是否等待池内任务完成后再返回；
    - aggressive_kill: 若为 True，则尝试强制终止所有残留子进程（优先使用 psutil）以避免僵尸或挂起子进程。
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
            logger.debug("shutdown_process_pool: calling pool.shutdown(wait=%s)", wait)
            print(f"shutdown_process_pool: calling pool.shutdown(wait={wait})")
            pool.shutdown(wait=wait)
            logger.debug("shutdown_process_pool: pool.shutdown returned")
            print("shutdown_process_pool: pool.shutdown returned")
        except Exception:
            logger.exception(
                "shutdown_process_pool: pool.shutdown raised, retrying with wait=False"
            )
            try:
                print("shutdown_process_pool: retrying pool.shutdown(wait=False)")
                pool.shutdown(wait=False)
                print("shutdown_process_pool: retry pool.shutdown returned")
            except Exception:
                logger.exception(
                    "shutdown_process_pool: retry pool.shutdown(wait=False) failed"
                )
                pass

        # 激进终止逻辑：优先使用 psutil 进行可靠的进程和子进程遍历与终止
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
            print(
                f"shutdown_process_pool: aggressive_kill pids={list(pids)} term_timeout={term_timeout} kill_timeout={kill_timeout}"
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
                logger.debug("shutdown_process_pool: terminating procs")
                print("shutdown_process_pool: terminating procs")
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
                logger.debug("shutdown_process_pool: enter wait loop until %s", end)
                print(f"shutdown_process_pool: enter wait loop until {end}")
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

                logger.debug("shutdown_process_pool: wait loop exited")
                print("shutdown_process_pool: wait loop exited")

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
                print("shutdown_process_pool: aggressive kill completed")
            except Exception:
                pass

        elif aggressive_kill and not _PSUTIL_AVAILABLE:
            # psutil 不可用：采用尽力而为的朴素终止策略，尝试向每个进程发送 SIGTERM/SIGKILL。
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
    """尽最大努力重建全局进程池。

    如果存在正在运行的池，先尝试（激进地）关闭它，然后返回一个新的进程池实例。
    注意：不要在持有 `_POOL_LOCK` 时调用会再次获取该锁的函数（比如
    `shutdown_process_pool`），以避免自锁死。目前实现先在无锁状态下
    调用关闭函数，然后在短锁段内将 `_PROCESS_POOL` 设为 None 并创建
    新的池。
    """
    global _PROCESS_POOL
    logger = logging.getLogger(__name__)
    try:
        if _PROCESS_POOL is not None:
            logger.info("_recreate_process_pool: shutting down existing pool")
            print(f"_recreate_process_pool: shutting down existing pool")
            # Call shutdown_process_pool without holding _POOL_LOCK to avoid deadlock
            try:
                shutdown_process_pool(wait=False, aggressive_kill=True)
            except Exception:
                logger.exception(
                    "_recreate_process_pool: error shutting down existing pool"
                )
    except Exception:
        logger.exception("_recreate_process_pool: unexpected error during shutdown")

    # Safely clear the global pool reference under the lock
    with _POOL_LOCK:
        _PROCESS_POOL = None

    try:
        if _PROMETHEUS_AVAILABLE:
            POOL_RESTARTS.inc()
    except Exception:
        pass
    logger.info("_recreate_process_pool: creating new pool")
    print(f"_recreate_process_pool: creating new pool")
    return _get_process_pool()


def _ensure_pool_health(
    pool: concurrent.futures.ProcessPoolExecutor,
) -> concurrent.futures.ProcessPoolExecutor:
    """Check pool worker pids and recreate pool if any worker process is dead.

    This is a best-effort health check: on POSIX we probe pids with os.kill(pid, 0).
    If any pid is missing or not runnable, we call _recreate_process_pool() to
    ensure future submissions have enough workers.
    """
    logger = logging.getLogger(__name__)
    try:
        proc_map = getattr(pool, "_processes", None)
        logger.debug("_ensure_pool_health: proc_map=%s", bool(proc_map))
        if not proc_map:
            return pool
        dead = False
        pids = []
        for p in list(proc_map.values()):
            pid = getattr(p, "pid", None)
            if not pid:
                # try to get from _popen
                popen = getattr(p, "_popen", None)
                pid = getattr(popen, "pid", None) if popen is not None else None
            pids.append(pid)
            if not pid:
                dead = True
                logger.debug("_ensure_pool_health: missing pid for worker object %r", p)
                break
            try:
                # os.kill(pid, 0) will raise OSError if process does not exist
                os.kill(pid, 0)
            except Exception:
                dead = True
                logger.warning("_ensure_pool_health: detected dead pid=%s", pid)
                break
        logger.debug("_ensure_pool_health: observed pids=%s dead=%s", pids, dead)
        if dead:
            try:
                print(
                    f"_ensure_pool_health: detected dead worker, pids={pids}, recreating pool"
                )
                logger.info(
                    "_ensure_pool_health: detected dead worker, recreating pool"
                )
                return _recreate_process_pool()
            except Exception:
                logger.exception("_ensure_pool_health: failed to recreate pool")
                return pool
        return pool
    except Exception:
        logger.exception("_ensure_pool_health: unexpected error")
        return pool


def get_sandbox_metrics() -> Dict[str, object]:
    """返回用于监控和告警的简要执行指标。

    返回值：包含平均/95% 延迟、样本数、执行总次数与超时次数的字典。
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
    llm_factory_path: Optional[str] = None,
    llm_session: Optional[Any] = None,
) -> Any:
    """在可复用的进程池中执行脚本并返回结果。

    实现细节：向 ProcessPoolExecutor 提交任务以重用工作进程，避免每次调用都 spawn 子进程。
    调用会阻塞最多 `timeout` 秒；如果超时则抛出 ScriptSandboxTimeout。
    """

    global _exec_count, _timeout_count, _PROCESS_POOL

    if timeout <= 0:
        raise ValueError("timeout must be positive")

    # 避免对大型 context 不必要的 JSON 序列化开销：若 context 已可 JSON 序列化则直接使用，
    # 否则回退到 deepcopy（或 JSON 回合）以将复杂对象转换为原语类型以保证安全传递。
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

    # 允许在测试/CI 中通过环境变量强制使用每调用单独子进程，以提高隔离性
    force = force_per_call or os.getenv("ECON_SIM_FORCE_PER_CALL") == "1"
    if force:
        logger = logging.getLogger(__name__)
        logger.debug(
            "execute_script: using per-call subprocess for script_id=%s", script_id
        )
        return _run_in_subprocess(
            code, safe_context, modules, timeout, llm_factory_path
        )

    # 如果在导入时检测到该解释器是通过不安全的入口点启动（例如 heredoc / stdin / -c），
    # 则优先使用每次调用的独立子进程路径，因为 spawn 在子进程中重执行父进程 main 模块时
    # 可能找不到正确的模块路径而导致失败。
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

    # 检测当前 Python 启动是否缺失真实的 main 文件名（例如通过 heredoc / <stdin> 运行），
    # 在这种情况下 multiprocessing.spawn 的子进程可能会尝试导入不存在的主模块而崩溃，
    # 因此回退到不依赖导入主模块的每次调用子进程路径。
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

        # 将 timeout 提交给 worker，以便 worker 在 CPU 密集型循环中能自我终止
        # pass llm args to worker (factory path is a dotted import string safe to pickle)
        # Ensure pool health before submitting: if worker processes are dead,
        # _ensure_pool_health will attempt to recreate the pool.
        try:
            pool = _ensure_pool_health(pool)
        except Exception:
            logger.exception("execute_script: _ensure_pool_health failed")
        try:
            print(
                f"execute_script: submitting to pool pid={os.getpid()} timeout={timeout}"
            )
            logger.debug("execute_script: submitting to pool (script_id=%s)", script_id)
            future = pool.submit(
                _pool_worker,
                code,
                safe_context,
                modules,
                float(timeout),
                llm_factory_path=llm_factory_path,
                llm_session=llm_session,
            )
            print(f"execute_script: submitted future={future}")
        except Exception:
            logger.exception(
                "execute_script: pool.submit failed, will attempt pool rebuild and retry"
            )
            try:
                with _POOL_LOCK:
                    try:
                        if _PROCESS_POOL is not None:
                            _PROCESS_POOL.shutdown(wait=False)
                    except Exception:
                        pass
                    _PROCESS_POOL = None
                    pool = _get_process_pool()
                print("execute_script: retrying submit after pool rebuild")
                future = pool.submit(
                    _pool_worker,
                    code,
                    safe_context,
                    modules,
                    float(timeout),
                    llm_factory_path=llm_factory_path,
                    llm_session=llm_session,
                )
            except Exception:
                logger.exception("execute_script: retry submit failed")
                raise
    except Exception as exc:
        # 如果进程池出现故障（例如子进程崩溃），尝试重置并重建一次进程池；
        # 若仍然失败，退回为在当前线程内执行 worker（无隔离）以避免使调用方崩溃。
        try:
            with _POOL_LOCK:
                try:
                    if _PROCESS_POOL is not None:
                        _PROCESS_POOL.shutdown(wait=False)
                except Exception:
                    pass
                _PROCESS_POOL = None
                pool = _get_process_pool()
            future = pool.submit(
                _pool_worker,
                code,
                safe_context,
                modules,
                float(timeout),
                llm_factory_path=llm_factory_path,
                llm_session=llm_session,
            )
        except Exception:
            # 最后手段回退：在当前进程内直接运行 worker（无隔离），但保持对外的异常语义一致。
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
        # 如果 future 还未开始运行，它可能仍在 ProcessPoolExecutor 的队列中；
        # 在宣布超时之前给予短暂宽限期以降低在极短 sandbox timeout 配置下出现误判的概率。
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
            # 任务已启动但超出提供的超时时间
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
        # 如果检测到进程池已损坏（常见于 spawn 无法导入 main 模块的情况），
        # 则尝试回退到 per-call 子进程路径以规避 ProcessPoolExecutor 的语义问题。
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
                return _run_in_subprocess(
                    code, safe_context, modules, timeout, llm_factory_path
                )
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

    # 结果在上文被解包后返回（可能为 None）
    return result


# Note: _pool_worker implementation is defined above (it must be top-level
# so ProcessPoolExecutor can pickle it). Removed duplicate placeholder.


def _noop() -> None:
    """用于预热工作进程的简单空操作函数。"""
    return None


def warm_process_pool(timeout: float = 1.0) -> None:
    """通过提交 noop 并等待来确保进程池已生成工作进程。

    这样可以降低首次真实任务因工作进程启动而被延迟的概率，从而减少在超时配置较短时
    触发误判的可能性。
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
    """在独立子进程中运行的入口函数。

    通过提供的连接发送回一个二元组：成功时为 ("ok", result)，失败时发送 ("err", traceback_string)。
    在退出前关闭连接以确保父进程在子进程意外终止时能收到 EOF。
    """
    try:
        _apply_resource_limits()
        try:
            pid = os.getpid()
            print(f"_subprocess_entry start pid={pid} code_len={len(code)}")
        except Exception:
            pass

        safe_builtins = _build_safe_builtins(allowed_modules)
        # Do not construct llm from environment in subprocess entry; rely on
        # explicit environment passing of factory path by the parent if needed.
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
    code: str,
    context: Dict[str, Any],
    allowed_modules: Set[str],
    timeout: float,
    llm_factory_path: Optional[str] = None,
) -> Any:
    """在独立子进程中运行代码并提供超时保证。

    若子进程在 `timeout` 内未返回结果，确保其被终止并抛出 ScriptSandboxTimeout。
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

    # 运行小型 runner：在子进程内执行代码并将结果以 JSON 输出
    # Runner will attempt to construct LLM via factory path from env var
    runner = (
        "import sys, os, json, traceback\n"
        "from econ_sim.script_engine.sandbox import _build_safe_builtins\n"
        "# Attempt to create per-execution llm session using factory from env.\n"
        "_llm = None\n"
        "try:\n"
        "    _factory_path = os.environ.get('ECON_SIM_LLM_FACTORY')\n"
        "    if _factory_path:\n"
        "        mod_name, attr = _factory_path.rsplit('.', 1)\n"
        "        mod = __import__(mod_name, fromlist=[attr])\n"
        "        factory = getattr(mod, attr)\n"
        "        try:\n"
        "            _llm = factory()\n"
        "        except Exception:\n"
        "            _llm = None\n"
        "    else:\n"
        "        _llm = None\n"
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

    # Pass factory path via environment for subprocess to import and call
    env = os.environ.copy()
    if llm_factory_path:
        env["ECON_SIM_LLM_FACTORY"] = llm_factory_path
    try:
        proc = subprocess.run(
            [sys.executable, "-c", runner],
            input=payload.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(0.1, float(timeout) + 1.0),
            env=env,
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
