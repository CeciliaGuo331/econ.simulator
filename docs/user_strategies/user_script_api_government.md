# 政府 (Government) 脚本 API（面向玩家）

目标读者：为政府（government）编写策略脚本的玩家。

## 一、可读状态（GovernmentState）

- `id` (str)
- `balance_sheet`
- `tax_rate` (float)
- `unemployment_benefit` (float)
- `spending` (float)
- `employees` (list)
- `debt_outstanding` (dict)
- `debt_instruments` (dict)

## 二、可下决策（GovernmentDecision）

- `tax_rate` (float)
- `government_jobs` (int)
- `transfer_budget` (float)
- `issuance_plan` (optional dict: {"volume": float, "min_price": Optional[float]}) — 提议国债发行计划（仅政府脚本允许）

## 三、示例脚本

```python
from econ_sim.script_engine.user_api import OverridesBuilder

def generate_decisions(context):
    b = OverridesBuilder()
    b.government(tax_rate=0.14, government_jobs=2, transfer_budget=1000.0)
    # 可选发行计划示例：
    # b.government(tax_rate=0.14, issuance_plan={"volume":10000.0, "min_price":0.98})
    return b.build()
```

## 四、与市场的交互

- issuance_plan 将在债券发行 / 拍卖逻辑中被考虑；请确保 `volume` 为正数且 `min_price` 合理。
- transfer_budget 与 spending 会影响 household 的可支配收入与 macro 指标。脚本应注意财政可持续性（过度发债可能影响长期经济）。

## 五、LLM 使用说明（示例与注意）

政府脚本可利用 LLM 草拟财政说明、评估财政冲击或建议发行计划文本。请遵循：尽量外部化复杂 LLM 调用，并在脚本内做严格解析与回退。

示例：在脚本内使用项目内的 LLM helper（更易复用）

```python
from econ_sim.utils.llm_session import create_llm_session_from_env

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
        from econ_sim.script_engine.user_api import OverridesBuilder
        b = OverridesBuilder()
        b.government(transfer_budget=transfer)
        return b.build()
    except Exception:
        return None
```

注意：政府决策往往对宏观有放大效应；在使用 LLM 建议时，应对返回值做量化限制（例如 upper/lower bounds）并在脚本中加入稳健的回退策略。