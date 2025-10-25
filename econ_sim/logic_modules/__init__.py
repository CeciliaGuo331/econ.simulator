"""新经济逻辑占位包。

本包包含一个临时 baseline stub 与一个最小的 orchestrator wrapper，便于开发期间快速迭代。
最终实现将扩展或替换此目录下的内容。
"""

from .baseline_stub import generate_baseline_decisions

__all__ = ["generate_baseline_decisions"]
