"""隔离执行用户策略脚本的轻量沙箱。"""

from __future__ import annotations

import builtins
import json
import multiprocessing
import traceback
from multiprocessing.connection import Connection
from types import MappingProxyType
from typing import Any, Dict, Iterable, Optional, Set

try:  # pragma: no cover - 平台兼容性
    import resource
except ImportError:  # pragma: no cover - Windows 等平台
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


_CTX = multiprocessing.get_context("spawn")


def execute_script(
    code: str,
    context: Dict[str, Any],
    *,
    timeout: float = DEFAULT_SANDBOX_TIMEOUT,
    script_id: Optional[str] = None,
    allowed_modules: Optional[Iterable[str]] = None,
) -> Any:
    """在受限环境中执行脚本并返回结果。"""

    if timeout <= 0:
        raise ValueError("timeout must be positive")

    safe_context = json.loads(json.dumps(context))
    parent_conn, child_conn = _CTX.Pipe()
    modules = (
        set(allowed_modules) if allowed_modules is not None else set(ALLOWED_MODULES)
    )

    proc = _CTX.Process(
        target=_worker,
        args=(code, safe_context, modules, child_conn),
        daemon=True,
    )
    proc.start()
    child_conn.close()

    try:
        if not parent_conn.poll(timeout):
            _terminate_process(proc)
            raise ScriptSandboxTimeout(
                f"脚本执行超过 {timeout} 秒"
                + (f" (id={script_id})" if script_id else "")
            )
        try:
            status, payload = parent_conn.recv()
        except EOFError as exc:
            raise ScriptSandboxError(
                "沙箱进程提前退出，可能因资源限制或运行时错误"
            ) from exc
    finally:
        parent_conn.close()
        proc.join(0.1)
        if proc.is_alive():
            _terminate_process(proc)

    if status == "ok":
        return payload
    raise ScriptSandboxError(payload)


def _terminate_process(proc: multiprocessing.Process) -> None:
    if not proc.is_alive():
        proc.join(0.05)
        return
    proc.terminate()
    proc.join(0.1)
    if proc.is_alive():
        proc.kill()
        proc.join(0.1)


def _worker(
    code: str,
    context: Dict[str, Any],
    allowed_modules: Set[str],
    conn: Connection,
) -> None:
    try:
        _apply_resource_limits()
        safe_builtins = _build_safe_builtins(allowed_modules)
        global_env = {"__builtins__": safe_builtins}
        local_env: Dict[str, Any] = {}
        exec(code, global_env, local_env)
        func = local_env.get("generate_decisions")
        if func is None or not callable(func):
            raise ScriptSandboxError(
                "脚本中必须定义可调用的 generate_decisions(context) 函数"
            )
        result = func(context)
        conn.send(("ok", result))
    except Exception as exc:  # pragma: no cover - 错误路径
        conn.send(("error", "".join(traceback.format_exception(exc))))
    finally:
        conn.close()


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
            raise ImportError(f"模块 {name!r} 不在许可列表中")
        return original_import(name, globals, locals, fromlist, level)

    safe["__import__"] = safe_import
    return MappingProxyType(safe)


def _module_allowed(name: str, allowed: Set[str]) -> bool:
    return any(name == module or name.startswith(f"{module}.") for module in allowed)


def _apply_resource_limits() -> None:
    if resource is None:  # pragma: no cover - 平台不支持
        return
    try:
        resource.setrlimit(
            resource.RLIMIT_CPU, (CPU_TIME_LIMIT_SECONDS, CPU_TIME_LIMIT_SECONDS)
        )
    except (ValueError, OSError):  # pragma: no cover - 最佳努力
        pass
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    except (ValueError, OSError):  # pragma: no cover - 最佳努力
        pass
