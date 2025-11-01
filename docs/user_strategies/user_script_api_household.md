# 家户（Household）脚本 API

本文按统一模板描述家户脚本的可用字段、经济学含义、格式和合法范围，以及如何构造决策与失效处理。

## 1. 交互契约

- 入口：`def generate_decisions(context)`。
- `context` 包含：`world_state`, `entity_state`, `config`, `script_api_version`, `agent_kind` ('household'), `entity_id`（通常为整数或字符串形式）。
- 推荐返回：使用 `OverridesBuilder.household(hid, ...)` 构造并返回 `build()` 的结果；返回 `None` 或非法结构将触发 baseline 回退。

## 2. 可读字段（逐项说明、格式与建议范围）

- `id` (int or str)
    - 含义：家户唯一标识。

- `balance_sheet` (object)
    - 子字段：
        - `cash` (float)：可用现金，>= 0。
        - `deposits` (float)：存款余额，>= 0。
        - `loans` (float)：负债（贷款），>= 0。
        - `inventory_goods` (float)：持有商品库存，>= 0。

- `bond_holdings` (dict)
    - 含义：债券 id -> 持仓数量。

- `skill` (float)
    - 含义：劳动技能水平，格式：浮点，取值通常在 [0, 1] 或更大，具体归一化参照 world_settings。

- `employment_status` (str)
    - 含义：如 'employed', 'unemployed', 'inactive' 等。

- `is_studying` (bool)
    - 含义：若为 True，则该家户在本周期从事学习，其 labor_supply 可能为 0。

- `education_level` (float)
    - 含义：累计教育/人力资本，格式：浮点。

- `labor_supply` (float)
    - 含义：可供给的劳动时间或单位，格式：浮点，>= 0。

- `wage_income` (float)
    - 含义：上期工资收入，格式：浮点。

- `last_consumption` (float)
    - 含义：上期实际消费支出。

- `lifetime_utility` (float)
    - 含义：累积的效用指标（只读，用于策略评估）。

此外，脚本可读 `world_state['macro']` 中的宏观统计量（如 `bond_yield`, `inflation`, `unemployment`）以辅助决策。

## 3. 可写决策字段（逐项说明、格式与合法范围）

通过 `OverridesBuilder.household(hid, ...)` 提交：

- `consumption_budget` (float)
    - 含义：计划用于本 tick 消费的预算（货币单位）。格式：非负浮点。
    - 建议约束：应小于等于 `cash + deposits + 可动用信贷`。

- `savings_rate` (float)
    - 含义：将可支配收入中分配给储蓄的比例（0-1）。格式：浮点。
    - 建议范围：[0.0, 1.0]。

- `labor_supply` (float)
    - 含义：本 tick 提供的劳动量。格式：非负浮点。若 `is_studying == True`，可能被忽略。

- `is_studying` (bool)
    - 含义：是否决定进入学习（一般仅在 daily tick 生效）。

- `education_payment` (float)
    - 含义：若进入学习，愿意支付的教育费用（货币单位）。格式：非负浮点。

- `deposit_order` / `withdrawal_order` (float)
    - 含义：请求把多少现金转入存款或从存款取出。格式：浮点，受现金与银行规则约束。

平台将对这些字段做类型和范围校验；若不满足，字段可能被拒绝或 clamp。

## 4. 决策失效 / fallback 行为

- 如果脚本抛出异常或返回 `None`：平台使用 baseline（例如简单预算规则、默认劳动选择）。
- 若返回字段超出 `config` 限制或类型错误：平台会尝试解析并 clamp，否则拒绝该字段并使用 baseline/上期值。

建议脚本在本地对返回值做严格校验并在异常情况下返回 `None` 或稳健默认值。

## 5. 示例（保守消费与学习决策）

```python
from econ_sim.script_engine.user_api import OverridesBuilder

def generate_decisions(context):
        try:
                ent = context.get('entity_state', {}) or {}
                hid = int(context.get('entity_id'))
                bs = ent.get('balance_sheet', {}) or {}
                cash = float(bs.get('cash', 0.0))
                wage = float(ent.get('wage_income', 0.0))

                # 简单规则：消费为现金的一小部分加上工资因子
                target_consumption = max(1.0, 0.05 * (cash + wage))

                b = OverridesBuilder()
                b.household(hid, consumption_budget=round(target_consumption,2), savings_rate=0.1)
                return b.build()
        except Exception:
                return None
```

## 6. 使用 LLM

- 可使用统一接口 `create_llm_session_from_env()` 但必须解析并验证返回；家户脚本由于权限与运行时限制，应尽量避免频繁或大型 LLM 调用。

---

若你希望，我可以把 `consumption_budget`、`savings_rate` 等字段的建议范围映射到仓库中的实际 `world_settings.yaml` 或测试 fixture，以提供更精确的基线与示例。 