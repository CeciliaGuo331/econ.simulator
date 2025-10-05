"""用户脚本执行相关的工具集。"""

from .registry import ScriptRegistry

# 全局单例，供 API 与调度器共享。
script_registry = ScriptRegistry()

__all__ = ["script_registry", "ScriptRegistry"]
