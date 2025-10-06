"""用户自定义策略的标准模板。"""

from __future__ import annotations

from typing import Any, Dict

from econ_sim.script_engine.user_api import OverridesBuilder


def generate_decisions(context: Dict[str, Any]) -> Dict[str, Any]:
    """根据平台提供的 `context` 构造决策覆盖。"""

    world = context["world_state"]
    config = context["config"]

    builder = OverridesBuilder()

    # 示例：为指定家户设置消费预算
    # builder.household(1, consumption_budget=120.0, savings_rate=0.2)

    # 示例：调整企业价格或产量
    # builder.firm(price=world["firm"]["price"] * 1.02)

    # TODO: 在此添加你的决策逻辑

    return builder.build()
