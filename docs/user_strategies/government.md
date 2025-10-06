# 政府策略编写指南

政府 (Government Agent) 负责税收、公共就业和转移支付政策，对总需求与收入分配具有直接影响。你的脚本将覆盖默认财政策略，是平台上唯一调节财政工具的依据。

## 1. 角色目标与约束

- **财政目标**：在稳增长、保就业与财政可持续之间取得平衡。常见任务包括在衰退期扩大支出、在过热期回收流动性。
- **硬约束**：
  - 预算约束：当期支出不能无限制增加，若超出税收与允许的赤字，系统会在结算时截断转移支付或新增岗位。
  - 税率上下限：税率会被强制裁剪到 `[0.0, 0.75]` 的安全区间。
- **软约束**：
  - 税率波动过大会引发家庭消费震荡。
  - 过多政府就业可能挤出企业招聘。

## 2. 可见数据结构

脚本可读取的关键字段包括：

| 类别 | 字段示例 | 说明 |
| ---- | -------- | ---- |
| **政府私有状态** | `world_state["government"]["tax_rate"]` | 当前个人所得税或综合税率。 |
| | `..."government_jobs"]` | 已在岗的政府员工数量。 |
| | `..."budget_balance"]` | 当期财政收支差额（正为盈余，负为赤字）。 |
| | `..."unemployment_benefit"]` | 基准失业补助水平，可辅助设定转移预算。 |
| **公共市场数据** | `world_state["macro"]` | 失业率、GDP、通胀、收入分布等宏观指标。 |
| | `world_state["firm"]["wage_offer"]`、`world_state["bank"]["loan_rate"]` | 评估市场工资与融资环境。 |
| | `world_state["households"]` | 为保障隐私，家户数据仅提供聚合统计，如 `average_income`（若配置中启用）。 |

所有字段都是数字或基础容器类型，便于直接进行运算或比较。

## 3. 可控制的决策项

`OverridesBuilder.government(...)` 支持以下参数：

| 字段 | 含义 | 建议范围 | 影响 |
| ---- | ---- | -------- | ---- |
| `tax_rate` | 当期综合税率。 | 0.0 ~ 0.75 | 影响财政收入与家户可支配收入。 |
| `government_jobs` | 公共岗位目标数量。 | 整数 ≥ 0 | 决定政府雇佣规模，匹配流程会尝试填补缺口。 |
| `transfer_budget` | 面向家户的转移支付总额。 | ≥ 0 | 在结算时按规则分配给失业或低收入家户。 |

系统会在结算阶段确保预算约束：如预算不足，将优先维持工资支出，再削减转移预算。

## 4. 默认策略回顾

基线脚本的主要思路：

1. 计算失业率与目标值（6%）的缺口 `unemployment_gap`。
2. 若失业率偏高，适度下调税率，力度约为 `gap * 0.1`，同时控制在 `[0.05, 0.45]` 区间。
3. 根据失业率增加公共岗位，使 `government_jobs` 至少覆盖失业人群的一定比例。
4. 依据失业率与家户数量估算转移预算，使其随衰退程度上升。

了解这些基线，便于你制定更精细的财政规则。

## 5. 样例：动态税率与逆周期支出

```python
from typing import Any, Dict

from econ_sim.script_engine.user_api import OverridesBuilder, clamp, fraction


def generate_decisions(context: Dict[str, Any]) -> Dict[str, Any]:
    world = context["world_state"]
    macro = world["macro"]
    gov = world["government"]

    builder = OverridesBuilder()

    unemployment = macro.get("unemployment_rate", 0.06)
    inflation = macro.get("inflation", 0.02)
    gdp = macro.get("gdp", 0.0)

    target_tax = clamp(gov.get("tax_rate", 0.15), 0.05, 0.6)
    cycle_adjustment = clamp((0.05 - unemployment) * 0.5, -0.05, 0.05)
    inflation_adjustment = clamp((inflation - 0.02) * 0.25, -0.03, 0.03)
    tax_rate = clamp(target_tax - cycle_adjustment + inflation_adjustment, 0.03, 0.6)

    households = max(1, len(world.get("households", {})))
    base_benefit = gov.get("unemployment_benefit", 50.0)
    transfer_budget = max(0.0, households * base_benefit * unemployment * 60)

    desired_jobs = int(gov.get("government_jobs", 0) + unemployment * households * 0.2)

    budget_balance = gov.get("budget_balance", 0.0)
    if budget_balance < 0:  # 赤字过大时渐进收缩
        tax_rate = clamp(tax_rate + 0.01, 0.03, 0.6)
        transfer_budget *= 0.9

    builder.government(
        tax_rate=round(tax_rate, 4),
        government_jobs=desired_jobs,
        transfer_budget=round(transfer_budget, 2),
    )

    return builder.build()
```

该策略按以下步骤运行：

1. 结合失业与通胀对税率做双向调节，抑制宏观波动。
2. 依据家户数量与失业率估算转移预算，确保逆周期支出。
3. 根据失业人口规模设定政府岗位目标。
4. 若财政赤字扩大，温和提高税率并收缩支出，保持可持续性。

## 6. 策略灵感

- **债务上限管理**：编写对 `gov["debt_ratio"]`（若配置开放）或预算余额的监控逻辑，在逼近上限时自动触发紧缩包。
- **定向补贴**：在宏观数据中识别特定群体（例如长期失业者比例），动态调整转移预算分配权重。
- **合作刺激方案**：根据企业 `wage_offer` 或银行 `loan_rate` 的变化，同步调整公共就业或税收，放大政策效果。

## 7. 自检清单

| 检查项 | 期望结果 |
| ------ | -------- |
| 数值合法性 | 税率在 0~0.75 内，岗位数为整数，转移预算非负。 |
| 预算一致性 | 在多期仿真中无大额赤字失控或转移预算被系统截断。 |
| 日志监控 | 平台日志无“字段不支持”或“值被裁剪”提示。 |
| 宏观追踪 | 失业率、财政余额随策略调整而变化，符合设计预期。 |

若在仿真中发现异常，可回滚到基础策略并逐步引入新逻辑，验证每块功能的效果。