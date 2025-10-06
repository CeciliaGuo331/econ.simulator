# 企业策略编写指南

企业 (Firm Agent) 是唯一的商品生产者，也是主要的私营雇主。你的脚本决定产量、价格与招聘策略，并影响家户消费与劳动市场的供需关系。本指南提供所有必须信息，帮助你在平台限定的数据与决策范围内实现自定义逻辑。

## 1. 角色目标与约束

- **核心目标**：在满足市场需求的同时保持库存稳健、维持盈利，并提供合适的工资来吸引劳动力。
- **硬约束**：
    - 现金余额不得低于 0，工资支出与原料成本会在结算阶段从现金中扣除。
    - 库存不能为负，计划产量会受到产能与员工数量影响（若产量远超可实现水平，引擎会自动截断）。
- **软约束**：
    - 工资过低会增加招聘难度，工资过高会侵蚀利润。
    - 计划产量偏离需求过大将导致库存成本或缺货风险。

## 2. 可见数据结构

企业脚本可以在 `context["world_state"]` 中读取以下稳定字段：

| 类别 | 字段示例 | 说明 |
| ---- | -------- | ---- |
| **企业私有状态** | `world_state["firm"]["balance_sheet"]` | 包含 `cash`、`deposits`、`inventory_goods` 等账户。 |
| | `world_state["firm"]["productivity"]` | 生产率（单位劳动力可产出量）。 |
| | `world_state["firm"]["employees"]` | 当前员工 ID 列表，可用于估算产能。 |
| | `world_state["firm"]["last_sales"]`、`"sales_history"` | 最近成交量数据。 |
| **公共市场数据** | `world_state["macro"]` | `inflation`、`unemployment_rate`、`gdp` 等宏观指标。 |
| | `world_state["bank"]["loan_rate"]`、`..."deposit_rate"]` | 参考资金成本。 |
| | `world_state["government"]["wage_offer"]` | 公共部门工资，可用于对比。 |

只使用以上字段编写逻辑，避免依赖其他临时字段，确保脚本在平台升级后仍可运行。

## 3. 可控制的决策项

通过 `OverridesBuilder.firm(...)` 可以设定以下数值：

| 字段 | 含义 | 建议范围 | 备注 |
| ---- | ---- | -------- | ---- |
| `planned_production` | 目标产量，商品单位。 | ≥ 0 | 引擎会根据员工与生产率评估可实现产量。 |
| `price` | 对外售价。 | > 0 | 会直接影响家户消费决策。 |
| `wage_offer` | 新招聘员工的工资。 | ≥ 0 | 影响劳动力市场吸引力。 |
| `hiring_demand` | 新增岗位数量。 | 整数 ≥ 0 | 由劳动力市场匹配机制处理。 |

若未设置某项，平台会沿用默认或上期值。请使用 `clamp` 等工具函数保持值在合理区间内，避免被系统裁剪。

## 4. 默认策略回顾

部署环境使用的基线脚本大致遵循以下原则：

1. 依据家户数量和最近消费推断需求 `demand_proxy`，并设定目标库存 `desired_inventory = household_count * 1.5`。
2. 根据库存缺口与需求组合计算 `planned_production`，防止缺货或积压。
3. 若库存偏低，价格小幅上调；库存偏高则下调。
4. 参考宏观失业率调节 `wage_offer`，失业率高时降低增长幅度。
5. 以计划产量与生产率估算所需工人数量，设置 `hiring_demand`。

了解默认逻辑有助于你针对特定情景做进一步的优化。

## 5. 样例：库存与资金双重调度

```python
from typing import Any, Dict

from econ_sim.script_engine.user_api import OverridesBuilder, clamp, moving_average


def generate_decisions(context: Dict[str, Any]) -> Dict[str, Any]:
        world = context["world_state"]
        firm = world["firm"]
        macro = world["macro"]

        builder = OverridesBuilder()

        inventory = firm["balance_sheet"].get("inventory_goods", 0.0)
        cash = firm["balance_sheet"].get("cash", 0.0)
        employees = len(firm.get("employees", []))
        productivity = max(firm.get("productivity", 0.1), 0.1)

        recent_sales = moving_average(firm.get("sales_history", []), window=3) or firm.get("last_sales", 0.0)
        target_inventory = recent_sales * 1.2
        inventory_gap = target_inventory - inventory

        planned_production = max(0.0, recent_sales + inventory_gap)
        max_capacity = employees * productivity * 1.1
        planned_production = min(planned_production, max_capacity)

        price_factor = clamp(1.0 + inventory_gap / max(target_inventory, 1.0) * 0.15, 0.85, 1.15)
        wage_factor = clamp(1.0 + (0.05 - macro.get("unemployment_rate", 0.05)) * 0.8, 0.85, 1.2)

        desired_headcount = int(planned_production / productivity)
        hiring_demand = max(0, desired_headcount - employees)

        builder.firm(
                planned_production=round(planned_production, 2),
                price=round(firm["price"] * price_factor, 2),
                wage_offer=round(firm.get("wage_offer", 80.0) * wage_factor, 2),
                hiring_demand=hiring_demand,
        )

        return builder.build()
```

这个示例：

1. 通过 `moving_average` 平滑销量数据，缓冲单期波动。
2. 使用库存目标调整产量与价格，确保库存稳定。
3. 将工资与宏观失业率挂钩，兼顾吸引力与成本。
4. 根据产量需求计算招聘目标，避免过度扩张。

## 6. 场景创意

- **利率冲击管理**：当贷款利率上升、资金成本走高时，降低 `planned_production` 或提高价格以保利润。
- **需求刺激合作**：在政府增加岗位或转移支付后，提前增加产量和雇佣，满足潜在消费增长。
- **库存动态折扣**：定义多级库存区间，当库存远高于目标时，逐步降低价格清仓。

可将这些逻辑封装成函数，在主流程中按条件切换，提高可读性。

## 7. 自检清单

| 项目 | 期望结果 |
| ---- | -------- |
| 数值范围 | 价格与工资为正，产量非负，招聘需求为整数。 |
| 可执行性 | 计划产量不超过可实现产能，避免频繁被引擎截断。 |
| 日志检查 | 无“字段不支持”或“值被裁剪”类警告。 |
| 经济效果 | 库存、利润、就业等时间序列符合策略设计目标。 |

在上传前，可通过本地调试或小规模仿真验证这些指标，确保脚本行为稳定。