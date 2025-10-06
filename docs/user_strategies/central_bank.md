# 央行策略编写指南

本指南面向“央行”用户，介绍如何编写并上传货币政策脚本以替换默认策略。仅央行角色可查看本页。

## 1. 策略目标

默认央行策略（`BaseCentralBankStrategy`）基于简化的泰勒规则：

- 按当前通胀与目标通胀的差值调整基准利率。
- 对失业率偏离目标作负向调节，避免经济过冷。
- 调整法定准备金率以应对失业率波动。

自定义策略时可关注：

- 兼顾 GDP、信贷增速等额外指标，实现多目标优化。
- 设定利率平滑、最小变动幅度或前瞻性指引。
- 与商业银行策略配合，确保金融稳定和信贷投放节奏。

## 2. 平台 API 摘要

- 入口函数：`generate_decisions(context)`。
- 使用 `OverridesBuilder.central_bank(policy_rate=..., reserve_ratio=...)` 修改政策工具。
- 上下文关键字段：
  - `context["world_state"]["macro"]`：通胀、失业率、GDP 等指标。
  - `context["world_state"]["central_bank"]`：当前政策利率、目标值。

## 3. 策略代码模板

```python
from typing import Any, Dict
from econ_sim.script_engine.user_api import OverridesBuilder, clamp

def generate_decisions(context: Dict[str, Any]) -> Dict[str, Any]:
    world = context["world_state"]
    macro = world["macro"]
    cb = world["central_bank"]

    inflation_gap = macro["inflation"] - cb["inflation_target"]
    unemployment_gap = macro["unemployment_rate"] - cb["unemployment_target"]

    policy_rate = clamp(cb["base_rate"] + 0.8 * inflation_gap - 0.4 * unemployment_gap, 0.0, 0.25)
    reserve_ratio = clamp(cb["reserve_ratio"] + 0.2 * unemployment_gap, 0.05, 0.35)

    builder = OverridesBuilder()
    builder.central_bank(policy_rate=policy_rate, reserve_ratio=reserve_ratio)
    return builder.build()
```

## 4. 提交步骤

1. 按模板编写脚本，确保遵守平台 API 限制。
2. 上传与挂载：
   - 使用 `POST /scripts` 上传到个人仓库。
   - 或 `POST /simulations/{id}/scripts` 直接挂载。
3. 如需将仓库中的脚本挂载到特定仿真，调用 `POST /simulations/{id}/scripts/attach`。
4. 新版脚本上传后立即生效，下一个 Tick 起执行新的策略。

> 央行角色仅可访问本指南与平台 API 文档。