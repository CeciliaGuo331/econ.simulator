# 商业银行 (Bank) 脚本 API（面向玩家）

目标读者：为商业银行（bank）编写策略脚本的玩家。

## 一、可读状态（BankState）

- `id` (str)
- `balance_sheet`: { `cash`, `reserves`, `deposits`, `loans`, `inventory_goods` }
- `deposit_rate` (float)
- `loan_rate` (float)
- `approved_loans` (dict)
- `bond_holdings` (dict)
- `equity` (只读计算属性)

## 二、可下决策（BankDecision）

- `deposit_rate` (float)
- `loan_rate` (float)
- `loan_supply` (float)

### 说明：

- `deposit_rate` 与 `loan_rate` 是你在本 tick 提议的利率；平台的 finance 模块会根据这些设定和其他市场条件处理存贷款流动。
- `loan_supply` 是你愿意在本 tick 内新增发放的贷款总量（实际是否发放取决于匹配与信用规则）。

## 三、示例脚本

```python
from econ_sim.script_engine.user_api import OverridesBuilder

def generate_decisions(context):
    b = OverridesBuilder()
    b.bank(deposit_rate=0.015, loan_rate=0.05, loan_supply=200.0)
    return b.build()
```

## 四、LLM 与风险注意（示例与安全建议）

商业银行可以利用 LLM 做风险评估、信用评分或生成解释文本，但务必采用严格的解析、校验与回退策略。

### 建议

- 在脚本中对 LLM 返回值做严格数值/格式校验；若解析失败或超时，应使用安全默认值或返回 None 以触发 baseline。
- 对于需要复杂计算或长时间运行的任务，优先使用外部服务生成信号并把结果以短小格式注入脚本可读取的 world_state 或配置中。

示例：使用项目内的 LLM helper（推荐在脚本可用时使用）
```python
from econ_sim.utils.llm_session import create_llm_session_from_env

def generate_decisions(context):
    try:
        session = create_llm_session_from_env()
        prompt = '给出当前贷款审批优先级（0-1），只返回数字。'
        resp = session.generate(prompt, max_tokens=5)
        text = resp.get('content', '')
        try:
            score = float(text.strip()) if text else 0.5
        except Exception:
            score = 0.5
        from econ_sim.script_engine.user_api import OverridesBuilder
        b = OverridesBuilder()
        b.bank(loan_supply=max(0.0, 1000.0 * score))
        return b.build()
    except Exception:
        return None
```
