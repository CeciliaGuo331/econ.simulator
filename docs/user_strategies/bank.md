# 商业银行策略编写指南

商业银行 (Commercial Bank Agent) 连接储户与贷款者，是货币传导与风险管理的关键环节。你的脚本决定存贷款利率以及本期可提供的信贷规模，将直接影响企业扩张与家庭消费。

## 1. 角色目标与约束

- **盈利目标**：维持正向利差并控制违约风险。
- **流动性目标**：满足央行准备金要求，确保随时可兑付存款。
- **硬约束**：
  - 实际准备金率不得低于央行公布的法定准备金率，系统会在结算阶段检查并调整贷款供给。
  - 存款利率必须低于贷款利率，若脚本给出相反结果，平台会自动裁剪为微正利差。
- **软约束**：
  - 利差过大将抑制贷款需求，过小会侵蚀利润。
  - 贷款供给过度可能导致违约率上升。

## 2. 可见数据结构

脚本可读取的核心字段：

| 类别 | 字段示例 | 说明 |
| ---- | -------- | ---- |
| **银行私有状态** | `world_state["bank"]["balance_sheet"]` | 包含 `cash`、`deposits`、`loans`、`reserves` 等账户。 |
| | `world_state["bank"]["pending_loans"]` | 当前待审批贷款列表，含 `requested` 金额与借款人类型。 |
| | `world_state["bank"]["default_rate"]`（若配置启用） | 近期违约率估计，可作为风险输入。 |
| **公共市场数据** | `world_state["central_bank"]` | `base_rate`、`reserve_ratio` 等政策变量。 |
| | `world_state["macro"]` | `inflation`、`gdp`、`credit_growth` 等宏观指标。 |
| | `world_state["government"]["bond_rate"]`（若启用） | 国债收益率，可对比贷款回报。 |

这些字段均为数字或由数字构成的列表，你可以直接遍历和统计。

## 3. 可控制的决策项

`OverridesBuilder.bank(...)` 支持以下参数：

| 字段 | 含义 | 建议范围 | 影响 |
| ---- | ---- | -------- | ---- |
| `deposit_rate` | 对储户支付的利率。 | ≥ 0，且 < `loan_rate` | 影响存款吸引力与资金成本。 |
| `loan_rate` | 对借款人收取的利率。 | ≥ 0 | 影响贷款需求与利润率。 |
| `loan_supply` | 当期可放贷额度（总量）。 | ≥ 0 | 引擎会根据待审批列表分配额度，多余申请会被拒。 |

系统会在结算时根据实际准备金率调整：若 `loan_supply` 导致准备金不足，会进行缩减，并在日志中提示。

## 4. 默认策略回顾

基线脚本的逻辑包括：

1. 从央行获取政策利率 `base_rate` 和法定准备金率 `reserve_ratio`。
2. 在政策利率基础上设定固定加点（约 2.5%），得到贷款利率，并限制在安全区间 `[0.02, 0.25]`。
3. 将存款利率设置为政策利率的 65%，确保稳定利差。
4. 根据 `存款 × (1 - 准备金率) - 已放贷` 计算可贷资金，确保满足监管要求。

你可以在此基础上引入风险溢价、周期调节等更细致的逻辑。

## 5. 样例：风险溢价与流动性缓冲

```python
from typing import Any, Dict

from econ_sim.script_engine.user_api import OverridesBuilder, clamp, moving_average


def generate_decisions(context: Dict[str, Any]) -> Dict[str, Any]:
    world = context["world_state"]
    bank = world["bank"]
    central_bank = world["central_bank"]
    macro = world["macro"]

    builder = OverridesBuilder()

    base_rate = central_bank.get("base_rate", 0.02)
    reserve_ratio = central_bank.get("reserve_ratio", 0.1)

    balance = bank.get("balance_sheet", {})
    deposits = balance.get("deposits", 0.0)
    loans = balance.get("loans", 0.0)
    reserves = balance.get("reserves", deposits * reserve_ratio)

    pending = bank.get("pending_loans", [])
    avg_request = moving_average([item.get("requested", 0.0) for item in pending], window=5) or 0.0

    default_rate = bank.get("default_rate", 0.02)
    credit_growth = macro.get("credit_growth", 0.0)

    risk_spread = clamp(default_rate * 1.5 + credit_growth * 0.2, 0.0, 0.05)
    liquidity_buffer = clamp(reserves / max(deposits, 1.0) - reserve_ratio, 0.0, 0.1)

    loan_rate = clamp(base_rate + 0.02 + risk_spread, 0.02, 0.3)
    deposit_rate = clamp(base_rate * 0.6 + liquidity_buffer * 0.5, 0.0, loan_rate - 0.005)

    max_loans = max(0.0, deposits * (1 - reserve_ratio) - loans)
    demand_adjusted = clamp(avg_request * len(pending), 0.0, max_loans * 1.2)
    loan_supply = min(max_loans, demand_adjusted)

    builder.bank(
        deposit_rate=round(deposit_rate, 4),
        loan_rate=round(loan_rate, 4),
        loan_supply=round(loan_supply, 2),
    )

    return builder.build()
```

策略要点：

1. 将违约率与信贷增长映射为风险溢价，动态调整贷款利率。
2. 以准备金缓冲（超额准备金比例）补贴存款利率，提高吸储能力。
3. 将贷款供给限制在监管允许范围内，并考虑实际申请需求。

## 6. 策略灵感

- **期限匹配**：若 `pending_loans` 包含不同期限，可对短期贷款设置低利率、长期贷款设置高利率（需扩展数据结构）。
- **逆周期政策**：在经济低迷（GDP 下降、失业率上升）时降低贷款利率并扩大供给，配合政府刺激。
- **信用配额**：为企业与家户分别设定最低供应额度，保障关键部门资金链。

## 7. 自检清单

| 检查项 | 期望结果 |
| ------ | -------- |
| 利率关系 | `loan_rate` > `deposit_rate` ≥ 0，且与宏观环境变化方向一致。 |
| 准备金合规 | 放贷后仍满足法定准备金率，无频繁被系统缩减的情况。 |
| 日志监控 | 无“值被裁剪”或“非法字段”警告。 |
| 风险表现 | 违约率、贷款增长率在策略目标范围内波动。 |

调试时可以临时打印关键变量，待策略稳定后再移除输出。这样能确保银行策略既符合监管要求，又能灵活响应市场。