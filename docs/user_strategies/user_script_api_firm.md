# 企业 (Firm) 脚本 API（面向玩家）

目标读者：为企业（firm）编写策略脚本的玩家。

## 一、context 可读内容（摘录）

- `context['entity_state']`（FirmState）关键字段：
  - `id` (str)
  - `balance_sheet`: { `cash`, `reserves`, `deposits`, `loans`, `inventory_goods` }
  - `price` (float)
  - `wage_offer` (float)
  - `planned_production` (float)
  - `productivity` (float)
  - `employees` (list[int])
  - `last_sales` (float)
- `world_state` 中你可见的宏观信息（`macro`）与 `features`。

## 二、脚本可下的决策字段（FirmDecision）

- `price` (float)
- `planned_production` (float)
- `wage_offer` (float)
- `hiring_demand` (int)

这些决定会被平台合并为 `TickDecisions.firm`，随后 labor_market / production / goods_market 等模块会使用这些字段进行撮合与计算。注意：实际产出由后端生产模块基于资本、劳动分配与技术计算；`planned_production` 是目标/计划。

## 三、构造返回值示例

```python
from econ_sim.script_engine.user_api import OverridesBuilder

def generate_decisions(context):
    b = OverridesBuilder()
    # 调整价格与生产计划
    b.firm(price=9.5, planned_production=120.0, wage_offer=85.0, hiring_demand=3)
    return b.build()
```

## 四、字段详解与使用建议

- `id` (str)
  - 唯一标识符（例如 "firm_1"）。

- `balance_sheet` (dict)
  - cash: 公司的现金头寸，用于支付工资与购买中间品。
  - inventory_goods: 可出售的商品库存，goods_market 会按价格与库存撮合销售。

- `price` (float)
  - 含义：当前商品标价。脚本可调整以应对库存与需求变化。

- `wage_offer` (float)
  - 含义：面向 labor_market 的招聘工资出价。labor_market 会用它来筛选候选人（结合候选人的 reservation_wage）。

- `planned_production` (float)
  - 含义：公司计划在本 tick 生产的目标产量（后端生产模块将考虑资本、人力与技术把它转化为实际产出）。脚本设置此字段作为目标。

- `productivity` (float)
  - 含义：公司内部平均工人效率，用作生产函数的参数。

- `employees` (list[int])
  - 当前雇佣的家户 id 列表。脚本可读以评估是否需要补招或裁员。

- `last_sales` (float)
  - 含义：上一次 tick 的商品销售总量（由 goods_market 写入），用于估算需求强度。

## 五、与劳动力市场的交互注意事项

- `hiring_demand` 与 `wage_offer` 会直接影响 labor_market 的匹配结果；提高 `wage_offer` 可以放宽 reservation_wage 的筛选并提高匹配概率。
- labor_market 在构建候选池时会：
  - 只包含 `decisions.households` 中 `labor_supply > 0` 的家户；
  - 排除 `is_studying == True` 的家户（无论该标记来自 state 还是来自该 tick 的决策）；
  - 使用家户的 skill/productivity 与随机扰动计算 matching_score 并按优先级分配岗位。

## 六、构造决策示例（带容错）

```python
from econ_sim.script_engine.user_api import OverridesBuilder

def generate_decisions(context):
    firm = context.get('entity_state', {}) or {}
    try:
        # 简单规则：略微下调价格以清库存
        price = max(0.1, float(firm.get('price', 10.0)) * 0.98)
        planned = max(0.0, float(firm.get('planned_production', 0.0)) + 10.0)
        wage = float(firm.get('wage_offer', 80.0))
        b = OverridesBuilder()
        b.firm(price=round(price,2), planned_production=planned, wage_offer=wage, hiring_demand=3)
        return b.build()
    except Exception:
        return None
```

## 七、LLM 使用建议（企业脚本）
- 场景：根据市场文字信息或外部情报生成定价策略时可调用 LLM 获取策略建议。
- 若要在脚本内部调用注入的 `llm`，请保证 prompt 短小并对返回结果做严格解析与回退处理。

## 八、常见问题与调试
- 产出差距：若 `last_sales` 显著小于 `planned_production`，检查 `labor_assignment`、firm `employees` 与 `price` 是否合理；planned_production 是目标，实际产出取决于后端生产模块。
- 招工失败：检查 `wage_offer` 是否高于目标候选人的 `reservation_wage`，并注意 `is_studying` 家户会被排除。

## 九、使用 LLM（进阶）
- 若脚本需要生成复杂定价策略，可以将简短 prompt 发送到平台 `/llm/completions` 或在脚本内使用注入的 `llm`（若可用）。注意脚本超时限制，避免长时间阻塞。

## 十、调试提示
- 若你发现 `last_sales` 与 `planned_production` 有差异：检查 `labor_assignment`、firm 的 `employees` 与生产要素（capital_stock、productivity）。
- 在开发阶段可先在脚本里返回少量字段以逐步验证效果。