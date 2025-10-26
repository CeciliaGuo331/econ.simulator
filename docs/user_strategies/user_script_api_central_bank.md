# 央行 (Central Bank) 脚本 API（面向玩家）

目标读者：为央行（central_bank）编写策略脚本的玩家。

## 一、可读状态（CentralBankState）

- `id` (str)
- `balance_sheet`
- `base_rate` (float)
- `reserve_ratio` (float)
- `inflation_target` (float)
- `unemployment_target` (float)
- `bond_holdings` (dict)

## 二、可下决策（CentralBankDecision）

- `policy_rate` (float)
- `reserve_ratio` (float)
- `omo_ops` (list of ops) — 每项结构：{"bond_id": str, "side": "buy"|"sell", "quantity": float, "price": float}

## 三、示例脚本

```python
from econ_sim.script_engine.user_api import OverridesBuilder

def generate_decisions(context):
    b = OverridesBuilder()
    b.central_bank(policy_rate=0.025, reserve_ratio=0.1)
    # 示例 OMO 操作：
    # b.central_bank(omo_ops=[{"bond_id":"g1","side":"buy","quantity":100.0,"price":1.0}])
    return b.build()
```

## 四、注意事项

- OMO 操作将通过市场/央行模块执行，价格与数量需谨慎设定。
- policy_rate 的微调会影响市场利率与银行行为；脚本需要考虑滞后与目标（inflation/unemployment targets）。

## 五、LLM 使用（推荐实践与示例）

央行策略常需情景分析与文本生成（例如政策说明）。平台支持两种方式使用 LLM：注入的 `llm` 对象（若管理员启用）或平台 HTTP 接口 `/llm/completions`（推荐，用于复杂或有配额/审计要求的场景）。

### 建议

- 优先使用平台 HTTP 接口以便统一鉴权、配额与审计；仅在性能敏感且管理员允许时，在沙箱中使用注入的 `llm` 对象。
- 在脚本内进行 LLM 调用时，保证 prompt 简短、解析严格并提供明确的回退值，以免脚本超时或返回不可解析结果。

示例：使用项目内的 LLM helper（同步、易于复用）

```python
from econ_sim.utils.llm_session import create_llm_session_from_env

def generate_decisions(context):
    try:
        session = create_llm_session_from_env()
        prompt = '基于最近的通胀与失业数据，建议下一步 policy_rate 调整（只返回数字）'
        resp = session.generate(prompt, max_tokens=20)
        text = resp.get('content', '')
        try:
            policy_rate = float(text.strip().split()[0]) if text else 0.025
        except Exception:
            policy_rate = 0.025
        from econ_sim.script_engine.user_api import OverridesBuilder
        b = OverridesBuilder()
        b.central_bank(policy_rate=policy_rate)
        return b.build()
    except Exception:
        return None
```

