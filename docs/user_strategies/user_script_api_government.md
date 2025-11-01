# 政府（Government）脚本 API

本文档按统一模板给出政府主体脚本的使用细则：交互契约、可读字段与其经济学含义与格式、如何构造决策与 fallback、决策字段逐项说明与示例。

## 1. 交互契约

- 必须实现 `def generate_decisions(context)`。
- `context` 包含 `world_state`, `entity_state`, `config`, `script_api_version`, `agent_kind`（'government'）, `entity_id`。
- 返回：使用 `OverridesBuilder.government(...)` 构造并返回 `build()` 的结果；返回 `None` 将触发 baseline。

## 2. 可读取的核心字段（逐项）

- `balance_sheet`（object）
  - 包含 `cash`, `debt_outstanding`, `deficit`（若有），均为浮点数且可为 0 或正数。

- `tax_rate` (float)
  - 当前税率，格式：0-1 的浮点（例如 0.2 表示 20%）。

- `unemployment_benefit` (float)
  - 失业救济规模/单位支付，格式：浮点。

- `spending` (float)
  - 当期/计划财政支出，格式：货币单位的浮点数。

- `debt_instruments` / `debt_outstanding` (dict/float)
  - 政府的债务结构与未偿总额（用于评估可持续性）。

- `world_state['macro']` 中的 `unemployment`, `gdp_growth`, `inflation` 等指标，用作决策依据。

## 3. 决策字段（逐项说明）

通过 `OverridesBuilder.government(...)` 提交：

- `tax_rate` (float)
  - 含义：本 tick 提议的税率调整（比例，0-1）。
  - 建议范围：`[0.0, 0.8]`（可根据 `config` 调整）。

- `government_jobs` (int)
  - 含义：本 tick 希望新增/调整的政府岗位数（整数，可为正/负以增加或裁员，若平台不支持负值则改为 0）。

- `transfer_budget` (float)
  - 含义：用于本 tick 的转移支付或一次性补贴的总预算（货币单位）。
  - 建议范围：`[0, debt_available_limit]`（请用 `balance_sheet` 与 `config` 校验）。

- `issuance_plan` (dict)
  - 含义：建议的国债发行计划，示例格式： `{"volume": float, "maturity": int, "coupon": float, "min_price": Optional[float]}`。
  - 校验：`volume > 0`, `maturity` 为正整数, `coupon` 通常为 [0, 1]。

## 4. 决策构造与失效策略

- 若脚本返回非法结构或抛异常：平台将使用 baseline（例如按历史税率/支出惯性调整）。
- 若字段超出 `config` 限制：平台可拒绝并发出告警或 clamp。
- 推荐在脚本内：
  - 检查财政可持续性（债务/GDP 或 `debt_outstanding`）并设置上限。
  - 对 `issuance_plan` 做基本校验并在解析 LLM 返回时强制类型转换与上下界限制。

## 5. 示例

```python
from econ_sim.script_engine.user_api import OverridesBuilder

def generate_decisions(context):
    try:
        ent = context.get('entity_state', {}) or {}
        bs = ent.get('balance_sheet', {}) or {}
        deficit = float(bs.get('deficit', 0.0))
        transfer = 0.0
        if deficit > 1000.0:
            transfer = 50.0
        transfer = max(0.0, min(transfer, 1e6))

        b = OverridesBuilder()
        b.government(tax_rate=float(ent.get('tax_rate', 0.15)), government_jobs=1, transfer_budget=transfer)
        return b.build()
    except Exception:
        return None
```

## 6. LLM 使用注意

- 仅使用 `create_llm_session_from_env()` 创建 LLM 会话；对 LLM 输出进行严格的类型与范围校验，解析失败时使用保守回退值或返回 `None`。

---

我可以把 `issuance_plan` 的 schema 与仓库中债券市场的实际实现（如债券 id/票息/到期结构）对齐，并生成更精确的校验逻辑，如果你需要我会继续。 
