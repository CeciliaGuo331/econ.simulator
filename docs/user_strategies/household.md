# 家户策略编写指南

本指南适用于“个体/家户”类型的用户，帮助你理解默认策略假设，并基于平台 API 编写自定义脚本。上传脚本后，可在支持的仿真实例中覆盖默认家户策略。

## 1. 策略目标

默认家户策略（`BaseHouseholdStrategy`）的核心逻辑：

- 先满足生存消费，再按偏好分配可支配收入。
- 根据收入与就业状况调节储蓄率（基准 20%，收入高于生存线 1.5 倍时上调）。
- 失业时提供满负荷劳动力供给，已就业时设定 0.8 的劳供。

自定义策略时，可围绕以下目标调整：

- 动态消费/储蓄：根据宏观指标（通胀、工资增速、利率）重新分配收入。
- 劳动力决策：设置自定义的求职/退出阈值，优化就业收益。
- 资产负债互动：当贷款利率过高或现金储备不足时主动调整储蓄率。

## 2. 平台 API 摘要

- 脚本入口为 `generate_decisions(context)`，上下文结构详见《[平台策略脚本 API 指南](./platform_api.md)》。
- 推荐使用 `econ_sim.script_engine.user_api.OverridesBuilder` 提供的 `household(hid, ...)` 方法。可设置的字段包括：
  - `consumption_budget`
  - `savings_rate`
  - `labor_supply`
- 可搭配 `clamp`、`fraction`、`moving_average` 等工具函数进行计算。

## 3. 策略代码模板

```python
from typing import Any, Dict
from econ_sim.script_engine.user_api import OverridesBuilder, clamp

def generate_decisions(context: Dict[str, Any]) -> Dict[str, Any]:
    world = context["world_state"]
    macro = world["macro"]

    builder = OverridesBuilder()
    for raw_id, data in world["households"].items():
        hid = int(raw_id)
        wage = data.get("wage_income", 0.0)
        precaution = clamp(macro["inflation"] * 0.5, 0.0, 0.3)
        builder.household(
            hid,
            consumption_budget=wage * (0.7 - precaution),
            savings_rate=0.2 + precaution,
        )

    return builder.build()
```

更多占位代码可参考仓库中的 `examples/scripts/strategy_template.py`。

## 4. 提交步骤

1. 在本地根据模板实现脚本，确保 `generate_decisions` 返回合法字典。
2. 通过 API 或前端上传：
   - `POST /scripts`：上传到个人脚本库（未挂载状态）。
   - `POST /simulations/{id}/scripts`：直接上传并挂载到指定仿真。
3. 使用 `POST /simulations/{id}/scripts/attach` 将个人脚本挂载到目标仿真实例。
4. 脚本上线后会在每个 Tick 中执行；若需更新，重新上传即可覆盖旧版本。

> 家户用户只需关注本指南与平台 API 文档，其余角色文档对你不可见。