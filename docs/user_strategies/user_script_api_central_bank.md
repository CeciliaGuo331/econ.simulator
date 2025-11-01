# 央行（Central Bank）脚本 API

本文档涵盖：与商业银行文档相同的 1-4 项（交互方式、可读信息字段与经济学含义、如何构造决策与失效时的 fallback、决策字段逐项说明），并针对央行职责补充 OMO/货币政策相关条目。

## 1. 平台与脚本的交互契约

- 入口函数：`def generate_decisions(context)`。
- `context` 含：`world_state`, `entity_state`, `config`, `script_api_version`, `agent_kind` (应为 'central_bank'), `entity_id`。
- 返回值建议：使用 `OverridesBuilder.central_bank(...)` 构造并返回 `build()` 的结果；返回 `None` 或抛异常将触发 baseline 回退。

## 2. 可读取的核心字段（逐项说明）

- `policy_rate` / `base_rate` (float)
    - 含义：当前记录的政策利率参考值（年化）。
    - 格式：浮点；建议范围：[0.0, 1.0]，但可支持负利率以 `config` 为准。

- `reserve_ratio` (float)
    - 含义：对商业银行的准备金率（比例，0-1）。
    - 格式：浮点，推荐范围：[0.0, 1.0]。

- `inflation_target`, `unemployment_target` (float)
    - 含义：央行的目标变量（例如通胀目标通常在 0.01-0.03 区间）。

- `balance_sheet`（央行持仓，包括 `reserves`, `bond_holdings`）

- `omo_history`（list）
    - 含义：最近若干期的 OMO（公开市场操作）记录，形如 `[{'tick': int, 'ops': [...]}, ...]`。

- `world_state['macro']`:
    - 含义：宏观数据（`inflation`, `gdp_growth`, `unemployment`, `bond_yield` 等），脚本应基于这些指标决定货币政策。

## 3. 决策字段（逐项、含义、格式与建议范围）

- `policy_rate` (float)
    - 含义：建议的新政策利率（年化）。
    - 格式：浮点。建议通过 `config` 中的步长与上下限做 clamp。

- `reserve_ratio` (float)
    - 含义：建议的法定准备金比例（对商业银行）。
    - 格式：浮点，范围 [0.0, 1.0]。

- `omo_ops` (list of dict)
    - 含义：公开市场操作建议，单项格式示例：
        {"bond_id": "BOND-2025-01", "side": "buy" | "sell", "quantity": 1000.0, "price": 99.5}
    - 平台会按市场规则执行或部分执行这些建议。`quantity` >= 0，`side` 必须为 "buy" 或 "sell"。

## 4. 决策失效与 fallback

- 若脚本返回 `None` 或抛异常：平台使用 baseline 央行逻辑（一般是维持上期政策或按规则小幅调整）。
- 非法字段格式或超过 `config` 限制：平台可能拒绝该字段或 clamp 到合法范围；被拒的关键字段将触发 baseline 或保留上期值。

## 5. 示例（简单货币政策规则）

```python
from econ_sim.script_engine.user_api import OverridesBuilder

def generate_decisions(context):
        try:
                ent = context.get('entity_state', {}) or {}
                macro = context.get('world_state', {}).get('macro', {}) or {}
                inflation = float(macro.get('inflation', 0.0))
                target = float(ent.get('inflation_target', 0.02))
                policy_rate = float(ent.get('policy_rate', 0.01))

                # 简单规则：以小步长向目标靠拢
                step = (context.get('config') or {}).get('policy_rate_step', 0.002)
                if inflation > target + 0.005:
                        policy_rate = round(policy_rate + step, 4)
                elif inflation < target - 0.005:
                        policy_rate = round(max(0.0, policy_rate - step), 4)

                b = OverridesBuilder()
                b.central_bank(policy_rate=policy_rate)
                return b.build()
        except Exception:
                return None
```

## 6. 关于 LLM 的使用

- 只能通过统一接口 `from econ_sim.utils.llm_session import create_llm_session_from_env` 创建会话并调用，必须对 LLM 返回做严格解析与范围校验，解析失败时回退为稳健值。

---

如果你希望，我可以把 `omo_ops` 的格式约束和示例逐项映射到 repo 中实际的债券结构（例如债券到期日、票息字段），或基于 `world_settings.yaml` 填充更精确的数值范围。


