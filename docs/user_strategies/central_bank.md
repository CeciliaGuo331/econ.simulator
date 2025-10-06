# 央行策略编写指南

央行 (Central Bank Agent) 通过调整政策利率与法定准备金率来影响货币条件和信贷供给。你的脚本构成货币政策的唯一输入，因此需要兼顾物价稳定、充分就业与金融稳定。

## 1. 角色目标与约束

- **政策双目标**：将通胀率锚定在目标附近，同时使失业率维持在自然水平。
- **金融稳定**：监控信贷扩张、违约率等指标，防止金融体系过热或收缩。
- **硬约束**：
  - 政策利率会被系统限制在 `[0.0, 0.35]` 区间，避免出现负利率或极端高利率。
  - 法定准备金率限制在 `[0.02, 0.5]`。
- **软约束**：
  - 频繁大幅调整会引发市场波动，可考虑设置平滑机制。
  - 如果同时上调利率与准备金率，需评估对商业银行贷款能力的双重影响。

## 2. 可见数据结构

央行脚本可在 `context["world_state"]` 中读取以下字段：

| 类别 | 字段示例 | 说明 |
| ---- | -------- | ---- |
| **央行私有状态** | `world_state["central_bank"]["base_rate"]` | 当前政策利率。 |
| | `..."reserve_ratio"]` | 当前法定准备金率。 |
| | `..."inflation_target"]`、`..."unemployment_target"]` | 官方目标值。 |
| | `..."stress_index"]`（若启用） | 金融压力指标，用于衡量风险。 |
| **公共宏观数据** | `world_state["macro"]["inflation"]`、`"unemployment_rate"` | 核心宏观指标。 |
| | `world_state["macro"]["gdp_gap"]`、`"credit_growth"` | 产出缺口、信贷扩张等辅助指标。 |
| | `world_state["bank"]["loan_rate"]`、`"default_rate"]` | 金融体系反馈信号。 |

这些数据全部为只读，更新策略后将在下一 Tick 中看到指标反馈。

## 3. 可控制的政策工具

`OverridesBuilder.central_bank(...)` 提供两个杠杆：

| 字段 | 含义 | 建议范围 | 影响 |
| ---- | ---- | -------- | ---- |
| `policy_rate` | 基准利率（政策利率）。 | 0.0 ~ 0.35 | 直接改变贷款成本和资金价格。 |
| `reserve_ratio` | 法定准备金率。 | 0.02 ~ 0.5 | 限制商业银行可贷资金，调节货币乘数。 |

系统会对输入值进行裁剪，并在日志中标注。如果需要保持平稳，可以自己在脚本中实现最大调整幅度限制。

## 4. 默认策略回顾

默认脚本使用简化的泰勒规则：

1. 计算通胀与目标的差距 `inflation_gap`。
2. 计算失业率与目标的差距 `unemployment_gap`。
3. 使用 `policy_rate = base_rate + 0.8 * inflation_gap - 0.4 * unemployment_gap` 调整利率，并限制在 `[0, 0.25]`。
4. 使用 `reserve_ratio = reserve_ratio + 0.15 * unemployment_gap` 调整准备金率，范围 `[0.05, 0.35]`。

你可以在此基础上引入更多指标、平滑项或前瞻指引。

## 5. 样例：前瞻指引与压力响应

```python
from typing import Any, Dict

from econ_sim.script_engine.user_api import OverridesBuilder, clamp, moving_average


def generate_decisions(context: Dict[str, Any]) -> Dict[str, Any]:
    world = context["world_state"]
    macro = world["macro"]
    cb = world["central_bank"]

    builder = OverridesBuilder()

    inflation = macro.get("inflation", 0.02)
    unemployment = macro.get("unemployment_rate", 0.05)
    gdp_gap = macro.get("gdp_gap", 0.0)
    credit_growth = macro.get("credit_growth", 0.0)

    target_inflation = cb.get("inflation_target", 0.02)
    target_unemployment = cb.get("unemployment_target", 0.05)

    inflation_gap = inflation - target_inflation
    unemployment_gap = unemployment - target_unemployment

    historical_inflation = moving_average(macro.get("inflation_history", []), window=4) or inflation
    forward_guidance = clamp((inflation - historical_inflation) * 0.6, -0.02, 0.02)

    stress_index = cb.get("stress_index", 0.0)

    policy_rate = cb.get("base_rate", 0.02)
    policy_rate += 0.7 * inflation_gap - 0.3 * unemployment_gap + 0.2 * gdp_gap + forward_guidance
    policy_rate = clamp(policy_rate, 0.0, 0.3)

    reserve_ratio = cb.get("reserve_ratio", 0.1)
    reserve_ratio += 0.1 * unemployment_gap - 0.2 * credit_growth + clamp(stress_index, -0.05, 0.05)
    reserve_ratio = clamp(reserve_ratio, 0.04, 0.4)

    builder.central_bank(
        policy_rate=round(policy_rate, 4),
        reserve_ratio=round(reserve_ratio, 4),
    )

    return builder.build()
```

策略亮点：

1. 通过 `inflation_history` 的滑动平均构造前瞻指引，避免对短期波动过度反应。
2. 引入产出缺口和信贷增长，综合评估经济冷热。
3. 用 `stress_index` 在金融压力升高时提高准备金率，稳定系统。

## 6. 策略灵感

- **利率走廊**：为政策利率设定上下走廊，当利率接近边界时自动放缓调整速度，保持市场预期稳定。
- **分阶段调控**：当通胀持续高于目标多个 Tick 时，触发“紧缩阶段”，提高准备金率并发布额外公告（可通过日志输出实现）。
- **联动协议**：监听商业银行的 `loan_rate` 或 `loan_supply`，在银行扩张过快时主动收紧政策。

## 7. 自检清单

| 检查项 | 期望结果 |
| ------ | -------- |
| 数值范围 | `policy_rate` 与 `reserve_ratio` 始终在允许区间内，无频繁被裁剪。 |
| 调整幅度 | 相邻 Tick 的政策变动平滑，无大幅跳变除非遇到极端冲击。 |
| 日志监控 | 无“字段不支持”“值被裁剪”等警告。 |
| 宏观反馈 | 通胀、失业率、信贷增长随着政策调整呈现合理方向变化。 |

通过这些步骤，你可以构建稳健且符合教学目标的货币政策脚本，为学生或研究者提供可重复的实验环境。