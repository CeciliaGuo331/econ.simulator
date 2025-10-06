# 家户策略编写指南

面向扮演“家户 (Household Agent)”角色的用户。家户负责消费、储蓄与劳动供给决策，同时受到预算和最低生活消费约束的限制。本指南帮助你理解默认行为、可观测数据与可调参数，从而编写具有经济学直觉的策略脚本。

## 1. 角色目标与约束

- **核心目标**：在满足最低生活消费的前提下，平衡即期效用（消费）与未来保障（储蓄），并在劳动力市场中做出响应。
- **硬约束**：
    - 预算约束：现金与存款合计不得为负。
    - 生存约束：每个 Tick 至少维持 `subsistence_consumption`（配置中给出，默认 40）。
- **软约束**：
    - 就业收益与闲暇偏好之间的权衡。
    - 对通胀、利率等宏观指标的敏感度。

> 这些约束由引擎在市场结算阶段强制执行。脚本返回的数值若违反约束，会被截断或导致脚本失效。

## 2. 我能看到哪些数据？

平台把家户可见的信息分成“私有状态”和“公开市场数据”两个部分，均通过 `context["world_state"]` 提供。下列表格列出了保证存在且稳定的字段。

| 数据类别 | 字段 | 说明 |
| -------- | ---- | ---- |
| **私有 `agent_state`** | `world_state["households"][<your_id>]["balance_sheet"]` | 包含 `cash`、`deposits`、`loans`。若你控制多个家户（如教学用集体账号），字典会包含所有授权家户的条目。 |
| | `..."wage_income"` | 最近一个 Tick 的工资收入。未就业时为 0。 |
| | `..."employment_status"` | `"employed_firm"`、`"employed_government"` 或 `"unemployed"`。 |
| | `..."last_consumption"` | 上一 Tick 的消费额，可辅助估算需求。 |
| **公共 `market_data`** | `world_state["macro"]` | `inflation`、`unemployment_rate`、`gdp`、`wage_index` 等宏观指标。 |
| | `world_state["firm"]["price"]` | 代表性企业的商品价格，决定消费成本。 |
| | `world_state["firm"]["wage_offer"]`、`world_state["government"]["wage_offer"]` | 劳动力市场公开工资。 |
| | `world_state["bank"]["deposit_rate"]`、`..."loan_rate"]` | 存贷款利率。 |

> **不要依赖未列出的键**：`world_state` 还可能出现调试用字段或其他家户的匿名统计。它们并非公开 API 的一部分，未来版本可能删除。脚本应只使用上述字段。

## 3. 可以控制哪些决策？

使用 `OverridesBuilder.household(household_id, ...)` 可为每个家户设置以下字段：

| 字段 | 含义 | 建议范围 | 常见用途 |
| ---- | ---- | -------- | -------- |
| `consumption_budget` | 本 Tick 的消费预算（货币单位），结算阶段按此上限扣除支出。 | `[subsistence_consumption, cash + wage_income]` | 稳定消费、进行逆周期调节。 |
| `savings_rate` | 工资收入分配给储蓄的比例（0~1）。对总储蓄额 `wage_income * savings_rate`。 | `0.0 ~ 0.8` | 应对高利率或失业风险，提高安全垫。 |
| `labor_supply` | 劳动力供给强度（0~1）。数值越高，求职意愿越强。 | `0.0 ~ 1.0` | 触发求职/跳槽或选择退出劳动力市场。 |

平台会自动裁剪超出区间的值，并在日志中写入警告。若想精准控制行为，请自行使用 `clamp` 等工具函数。

## 4. 默认策略是怎么做的？

`BaseHouseholdStrategy`（部署时使用）遵循以下规则：

1. **安全垫估计**：根据宏观失业率计算预防性储蓄 `precaution = clamp(0.12 + unemployment_rate * 0.25, 0.1, 0.45)`。
2. **消费调整**：
     - 先取 `subsistence = 40` 作为最低消费线。
     - 将工资与少量现金收益综合后乘以 `inflation_factor = clamp(1 - inflation * 0.5, 0.7, 1.1)`，缓冲物价变动。
3. **储蓄率设定**：直接把 `precaution` 当作储蓄率，确保失业风险越高、储蓄越多。
4. **劳供**：失业时返回 `labor_supply = 1.0`，已就业则降低到 0.85，模拟“在职但仍保留部分求职意愿”。

理解该基线后，你可以逐条替换：例如，将 `subsistence` 设置为随工资增长的函数，或把 `labor_supply` 与岗位工资差距关联。

## 5. 实现蓝图：一步步构建策略

```python
from typing import Any, Dict

from econ_sim.script_engine.user_api import OverridesBuilder, clamp, fraction


def generate_decisions(context: Dict[str, Any]) -> Dict[str, Any]:
        world = context["world_state"]
        macro = world["macro"]

        builder = OverridesBuilder()

        for raw_id, data in world["households"].items():
                hid = int(raw_id)
                sheet = data.get("balance_sheet", {})
                wage_income = data.get("wage_income", 0.0)
                cash = sheet.get("cash", 0.0)
                deposits = sheet.get("deposits", 0.0)

                precaution = clamp(0.15 + macro.get("unemployment_rate", 0.0) * 0.2, 0.05, 0.6)
                inflation_shock = clamp(1.0 - macro.get("inflation", 0.0), 0.6, 1.2)

                desired_consumption = (wage_income + fraction(deposits, 12)) * (1 - precaution)
                consumption_budget = max(40.0, desired_consumption) * inflation_shock

                employment_status = data.get("employment_status", "").lower()
                labor_supply = 1.0 if "unemployed" in employment_status else clamp(0.6 + precaution * 0.8, 0.5, 0.95)

                builder.household(
                        hid,
                        consumption_budget=round(consumption_budget, 2),
                        savings_rate=round(precaution, 3),
                        labor_supply=round(labor_supply, 3),
                )

        return builder.build()
```

逐步实现建议：

1. **定义指标**：提取工资、现金、公共指标等基础变量。
2. **构造洞察**：通过 `fraction`、`moving_average` 等工具构建自定义指标，例如“过去 4 Tick 的平均工资”。
3. **设定规则**：写出直观的 if/else 或线性组合，确保满足约束。
4. **生成决策**：调用 `builder.household(...)` 并返回 `builder.build()`。

## 6. 场景灵感

- **高通胀防御**：物价上涨时提升储蓄率、主动减少消费篮子中的可选品支出。
- **失业救助策略**：结合政府岗位工资和企业工资差距，动态调整 `labor_supply`，引导家户优先申请薪资更高的一方。
- **负债管理**：当 `loans/cash` 比例过高时，下调消费预算或提高储蓄率，防止违约。

你可以把多个场景写成独立函数，再根据宏观环境选择调用，实现模块化的策略模板。

## 7. 自检与排错

| 检查项 | 通过标准 |
| ------ | -------- |
| 输入范围 | `consumption_budget ≥ 0`，`0 ≤ savings_rate ≤ 1`，`0 ≤ labor_supply ≤ 1`。 |
| 约束一致性 | 消费预算不超过现金 + 工资之和，储蓄率不导致预算为负。 |
| 日志告警 | 平台日志无“字段不支持”或“取值被裁剪”提示。 |
| 策略表现 | 仿真中消费、储蓄、就业率的走势与预期一致。 |