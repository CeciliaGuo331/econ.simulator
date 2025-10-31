## 家户脚本 API（简明、与当前实现一致）

本文件列出家户（household）脚本在 sandbox 中可读的精确字段（白名单），并给出简单示例。

重要说明：为了保护玩家隐私，家户脚本只会看到自己的 household 条目；非家户脚本不会得到完整的 `households` 列表。若需要宏观统计量，请使用 `world_state['macro']` 或平台提供的聚合字段（如有）。

一、context 快速参考

- `world_state`：被裁剪的世界快照（dict）。对家户脚本包含 `tick`, `day`, `features`, `macro`，以及该家户在 `households` 下的单条记录（键为其 id）；不包含其他家户的私有数据。
- `entity_state`：该家户的序列化状态（等同于 `world_state['households'][id]`），一个 dict，仅包含下列白名单字段（及其子字段）。
- `config`：只读世界配置（policies 等）。
- `script_api_version`：int。
- `agent_kind`：字符串 `'household'`。
- `entity_id`：实体 id（字符串形式）。

二、家户可读字段白名单（entity_state / household 条目）

仅下列字段可被家户脚本读取（若不存在则视为空或 0）：

- `id` (int)
- `balance_sheet` (object): 子字段：`cash`, `deposits`, `loans`, `inventory_goods`
- `skill` (float)
- `employment_status` (str)
- `is_studying` (bool)
- `education_level` (float)
- `labor_supply` (float)
- `wage_income` (float)
- `last_consumption` (float)
- `lifetime_utility` (float)

三、可写决策字段（HouseholdDecision）

家户脚本可返回的决策字段与语义（通过 `OverridesBuilder.household(hid, ...)` 提交）：

- `consumption_budget` (float)
- `savings_rate` (float)
- `labor_supply` (float)
- `is_studying` (bool) — 仅在 daily tick 有效；若为 True，建议同时设置 `education_payment`。
- `education_payment` (float)
- `deposit_order` / `withdrawal_order` (float)

四、简明示例（推荐使用 OverridesBuilder）

```python
from econ_sim.script_engine.user_api import OverridesBuilder, clamp

def generate_decisions(context):
    # 获得当前实体 id 与序列化状态（仅为本家户）
    hid_raw = context.get('entity_id')
    if hid_raw is None:
        return {}
    try:
        hid = int(hid_raw)
    except Exception:
        return {}

    ent = context.get('entity_state') or {}
    bs = ent.get('balance_sheet', {})
    cash = float(bs.get('cash', 0.0))
    deposits = float(bs.get('deposits', 0.0))
    wage = float(ent.get('wage_income', 0.0))

    # 使用仅有字段做预算决定（避开未暴露的私有字段）
    liquid = cash + deposits
    target_consumption = max(1.0, (0.05 * liquid + 0.5 * wage))

    features = context.get('world_state', {}).get('features', {}) or {}
    is_daily = bool(features.get('is_daily_decision_tick'))

    builder = OverridesBuilder()
    # 每日决策可同时选择学习
    if is_daily and float(ent.get('education_level', 0.0)) < 0.4:
        builder.household(hid, consumption_budget=round(target_consumption,2), savings_rate=0.1, is_studying=True, education_payment=2.0)
    else:
        builder.household(hid, consumption_budget=round(target_consumption,2), savings_rate=0.1)

    return builder.build()
```

五、常见错误快速排查

- 若脚本期望读取其他家户（例如 `world_state['households']` 中的所有条目）会发现该字典只包含本家户；不要依赖它来做全局聚合。
- 若需要宏观统计量，应优先读取 `world_state['macro']` 或请求平台提供的受控聚合字段。

更多示例与细节请参见同目录下其他主体文档。