
# 政府 (Government) 脚本 API（面向玩家）

目标读者：为政府（government）编写策略脚本的玩家。本指南与 household 文档保持一致，包含 context 说明、可写字段、示例、LLM 使用约定（仅支持一种调用方式）与排错建议。

## 重要前置
- 入口函数：你的脚本必须定义 `def generate_decisions(context)`，返回决策覆盖（overrides）。
- 推荐使用 `econ_sim.script_engine.user_api.OverridesBuilder()` 构造返回值。
- 注意隐私/可见性：脚本看到的 `world_state` 会被裁剪成只包含与政府相关或公开的内容。

## 一、context 的结构与如何使用

- `world_state`：包含宏观变量（`macro`）、特性（`features`）、以及与你相关的公开市场/主体信息。
- `entity_state`：GovernmentState 的序列化结构，常见字段如下。
- `config`：财政政策参数、发行约束等。
- `script_api_version`、`agent_kind`、`entity_id` 同 household 文档。

常见 `entity_state` 字段说明：

- `id` (str)
- `balance_sheet` (dict)
- `tax_rate` (float)
- `unemployment_benefit` (float)
- `spending` (float)
- `employees` (list)
- `debt_outstanding` / `debt_instruments`（dict）

## 二、可下决策字段（GovernmentDecision）与说明

允许提交的字段（示例）：

- `tax_rate` (float)
  - 本 tick 提议的税率调整（请检查 `config` 中的上限/下限与步长）。
- `government_jobs` (int)
  - 本 tick 希望新增的政府岗位数量。
- `transfer_budget` (float)
  - 用于转移支付或一次性补贴的预算（货币单位）。
- `issuance_plan` (dict, optional)
  - 建议的国债发行计划，例如 `{"volume": float, "min_price": Optional[float]}`，平台会在债券发行模块中考虑该建议并与市场交互。

注意：政府决策可能对宏观变量有放大效应，一个稳健的脚本应包含边界检查与财政可持续性约束（避免无上限地发债）。

## 三、如何构造与返回覆盖（示例）

```python
from econ_sim.script_engine.user_api import OverridesBuilder
from econ_sim.utils.llm_session import create_llm_session_from_env

def generate_decisions(context):
    try:
        ent = context.get('entity_state', {}) or {}
        deficit = float(ent.get('balance_sheet', {}).get('deficit', 0.0))
        transfer = 0.0
        if deficit > 1000:
            transfer = 50.0
        b = OverridesBuilder()
        b.government(tax_rate=float(ent.get('tax_rate', 0.15)), government_jobs=1, transfer_budget=transfer)
        return b.build()
    except Exception:
        return None
```

## 四、在脚本中使用 LLM（统一、受支持的方法）

平台对脚本内调用 LLM 的方式只支持一种：

    from econ_sim.utils.llm_session import create_llm_session_from_env

示例：

```python
from econ_sim.utils.llm_session import create_llm_session_from_env
from econ_sim.script_engine.user_api import OverridesBuilder

def generate_decisions(context):
    try:
        session = create_llm_session_from_env()
        prompt = '基于当前财政赤字与失业率，建议本轮 transfer_budget 的调整（只返回数字）'
        resp = session.generate(prompt, max_tokens=10)
        text = resp.get('content', '')
        try:
            transfer = float(text.strip()) if text else 0.0
        except Exception:
            transfer = 0.0

        # 量化限制与回退
        transfer = max(0.0, min(transfer, 1e6))
        b = OverridesBuilder()
        b.government(transfer_budget=transfer)
        return b.build()
    except Exception:
        return None
```

注意：对 LLM 输出必须做上下界约束与类型校验；在解析失败时使用稳健回退值。

## 五、常见错误与排查

- `issuance_plan` 未被采纳：检查字段格式与平台约束（volume > 0，min_price 在合理范围）。
- 财政不可持续：频繁大额 `transfer_budget` 与 `issuance_plan` 将导致债务膨胀并影响长期指标，请在脚本中加入上限约束。
- 脚本超时或异常：加入异常保护并在本地 dry-run 验证返回结构。

## 六、本地 dry-run 与测试

使用 `dry_run_generate` 模板（参见 household 文档）在本地构造 `sample_ctx` 并验证 `generate_decisions` 的返回结构。
