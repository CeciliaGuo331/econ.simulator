# 企业策略编写指南

本指南面向“企业”用户，帮助你使用平台提供的脚本接口自定义生产、定价与用工策略。上传的脚本将覆盖默认企业策略，仅对该角色可见。

## 1. 策略目标

默认企业策略（`BaseFirmStrategy`）的核心假设：

- 按照家户数量与最低消费需求估计目标库存。
- 根据库存缺口与历史销量推算下一期产量。
- 按库存位置轻微调整价格（库存低 → 提价，库存高 → 降价）。
- 结合计划产量与生产率决定招聘需求，并围绕基准工资定价。

自定义策略可聚焦：

- 更灵活的库存与产量管理（结合季节、宏观需求）。
- 定价策略：基于通胀、银行利率或竞争对手指标调整。
- 招聘与工资决策：引入边际收益或劳动力市场紧张度判断。

## 2. 平台 API 摘要

- 入口函数：`generate_decisions(context)`。
- 使用 `OverridesBuilder.firm(...)` 可覆盖字段：`price`、`planned_production`、`wage_offer`、`hiring_demand`。
- 建议结合 `builder.household` 对关键客户或员工进行配合调整，以形成闭环策略。

## 3. 策略代码模板

```python
from typing import Any, Dict
from econ_sim.script_engine.user_api import OverridesBuilder, moving_average

def generate_decisions(context: Dict[str, Any]) -> Dict[str, Any]:
    world = context["world_state"]
    firm = world["firm"]
    macro = world["macro"]

    builder = OverridesBuilder()

    recent_sales = moving_average(firm.get("sales_history", []), window=4) or firm["last_sales"]
    demand_signal = recent_sales * (1 + macro["gdp_growth"] * 0.5)

    builder.firm(
        planned_production=demand_signal,
        price=firm["price"] * (1 + macro["inflation"] * 0.3),
        hiring_demand=max(0, int(demand_signal / max(firm["productivity"], 0.1)) - len(firm["employees"])),
    )

    return builder.build()
```

更多占位代码与说明参阅 `examples/scripts/strategy_template.py`。

## 4. 提交步骤

1. 编写并测试脚本，确保满足 API 约束。
2. 上传与挂载：
   - `POST /scripts` 上传至个人仓库，便于重复使用。
   - 或 `POST /simulations/{id}/scripts` 直接挂载。
3. 使用 `POST /simulations/{id}/scripts/attach` 将已上传脚本挂载到指定仿真实例。
4. 更新脚本时重复上传操作，系统会自动替换旧版本。

> 企业用户无需查看其他角色文档。