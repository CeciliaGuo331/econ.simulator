# 企业（Firm）脚本 API

本文档为编写企业主体脚本的玩家提供完整契约：
- 脚本与平台如何交互（入口与返回约定）
- 可读字段与经济学含义、格式与建议范围
- 决策构造、失效与 fallback 策略
- 决策字段逐项说明与示例代码

假设：字段名和语义基于仓库当前实现；如果你的世界配置 (`config` 或 world_settings) 中对某些字段有更严格限制，请以实际配置为准。

## 1. 交互契约

- `generate_decisions(context)` 必须存在并返回覆盖（建议使用 `OverridesBuilder.firm(...)`）。
- 若返回 `None` / 抛异常 / 返回非法结构，平台使用 baseline（后端默认逻辑）。

## 2. 脚本可读字段（示例与说明）

- `id` (str)：企业 id。
- `balance_sheet` (dict)：至少包含 `cash`（float, >=0）、`inventory_goods`（float >=0）、可能有 `debt`、`capital_stock`。
- `price` (float)：当前商品标价。格式：浮点，建议 > 0。通常市场撮合器会使用 `price` 与 `inventory` 决定成交量。
- `wage_offer` (float)：面向 labor_market 的工资报价，格式：浮点，建议 >= 0。
- `planned_production` (float)：上期/当前计划产量，格式：非负浮点。
- `productivity` (float)：生产效率，格式：浮点，可能大于 0。
- `employees` (list[int])：当前雇佣的家户 id 列表。
- `last_sales` (float)：上期实际成交量，用于评估需求。

经济学含义与可用性：这些字段用于决定价格、生产计划与雇佣策略。脚本应以 `last_sales` 为更可信的需求信号，而 `planned_production` 为自身的供给意向。

## 3. 决策字段（逐项，格式与建议范围）

使用 `OverridesBuilder.firm(price=..., planned_production=..., wage_offer=..., hiring_demand=...)`。

- `price` (float)
  - 含义：本 tick 新的商品标价。格式：浮点，建议 `price > 0`。
  - 建议范围：`[0.01, +inf)`，小于 0 会被拒绝或 clamp。

- `planned_production` (float)
  - 含义：目标产量（投入给生产模块）。格式：非负浮点。
  - 建议范围：`[0, capacity]`（capacity 可基于 `capital_stock` 与 `employees` 估算）。

- `wage_offer` (float)
  - 含义：本 tick 对求职者的工资报价。格式：浮点，建议 >= 0。

- `hiring_demand` (int)
  - 含义：希望新增招聘的岗位数（整数）。格式：非负整数。

处理不合法值：平台会尝试类型转换或拒绝并 fallback；脚本应在本地 clamp 并保证类型正确。

## 4. 决策构造与出错回退

- 推荐：把所有决策包装到 `OverridesBuilder.firm(...)` 中返回。
- 若你希望安全回退：脚本应捕获异常并返回 `None`（触发 baseline）或返回保守决策（例如保持上期价格，设定 `hiring_demand=0`）。

## 5. 示例（含 LLM 用法与解析防护）

```python
from econ_sim.script_engine.user_api import OverridesBuilder
from econ_sim.utils.llm_session import create_llm_session_from_env

def generate_decisions(context):
    try:
        ent = context.get('entity_state', {}) or {}
        curr_price = float(ent.get('price', 10.0))
        last_sales = float(ent.get('last_sales', 0.0))

        # 简单规则：若上期销量高于库存，则小幅涨价
        inv = float(ent.get('balance_sheet', {}).get('inventory_goods', 0.0))
        price = curr_price
        if last_sales > inv * 0.8:
            price = round(curr_price * 1.02, 2)

        # 使用 LLM 获取一个比例建议（可选），但必须解析并 clamp
        try:
            session = create_llm_session_from_env()
            resp = session.generate('建议本期价格调整比例（只返回数字）', max_tokens=10)
            suggested = float(resp.get('content', '').strip().split()[0])
            # 限制调整幅度
            suggested = max(-0.1, min(0.1, suggested))
            price = round(curr_price * (1 + suggested), 2)
        except Exception:
            pass

        b = OverridesBuilder()
        b.firm(price=price, planned_production=max(0.0, last_sales + 5), wage_offer=float(ent.get('wage_offer', 50.0)), hiring_demand=1)
        return b.build()
    except Exception:
        return None
```

## 6. 常见故障与排查

- `planned_production` 无法全部转化为产出：检查 `employees`、`productivity` 与资本约束。
- 招工失败：提高 `wage_offer` 或降低 `hiring_demand`。
- 返回非法类型或字段：使用 `OverridesBuilder` 并在本地 dry-run 验证返回结构。

需要我把 `capacity`、`wage_offer` 的合理范围与仓库中可能存在的 `world_settings.yaml` 参数做映射并生成更精确的建议吗？
