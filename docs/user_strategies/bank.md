# 商业银行策略编写指南

本指南适用于“商业银行”用户，帮助你自定义存贷利率与信贷供给策略。上传的脚本会覆盖默认银行策略，仅该角色可见。

## 1. 策略目标

默认商业银行策略（`BaseBankStrategy`）逻辑：

- 以央行基准利率为基础设置固定利差（+3%），并裁剪到安全区间。
- 存款利率按基准利率的 60% 给定，确保正向利差。
- 计算可贷资金：`存款 × (1 - 准备金率) - 已放贷`。

你可以考虑的高级目标：

- 利差自适应：根据不良贷款率、宏观风险溢价调整。
- 存贷配比：对不同客户群体设置信贷优先级或额度限制。
- 与政府/企业协同：在财政刺激或企业扩张时配合调整贷款供应。

## 2. 平台 API 摘要

- 入口函数：`generate_decisions(context)`。
- 使用 `OverridesBuilder.bank(...)` 可覆盖字段：`deposit_rate`、`loan_rate`、`loan_supply`。
- 上下文中的关键数据：
  - `context["world_state"]["bank"]`：银行资产负债表、贷款映射。
  - `context["world_state"]["central_bank"]`：政策利率及法定准备金。

## 3. 策略代码模板

```python
from typing import Any, Dict
from econ_sim.script_engine.user_api import OverridesBuilder, clamp

def generate_decisions(context: Dict[str, Any]) -> Dict[str, Any]:
    world = context["world_state"]
    bank = world["bank"]
    central_bank = world["central_bank"]

    policy_rate = central_bank["base_rate"]
    credit_demand = sum(loan.get("requested", 0.0) for loan in bank.get("pending_loans", []))

    builder = OverridesBuilder()
    builder.bank(
        loan_rate=clamp(policy_rate + 0.015 + credit_demand * 0.0001, 0.02, 0.25),
        deposit_rate=clamp(policy_rate * 0.7, 0.0, 0.2),
        loan_supply=max(0.0, bank["balance_sheet"]["deposits"] * (1 - central_bank["reserve_ratio"]) - bank["balance_sheet"]["loans"]),
    )

    return builder.build()
```

## 4. 提交步骤

1. 按模板实现脚本并本地测试。
2. 通过接口上传：
   - `POST /scripts` 保存到个人仓库。
   - `POST /simulations/{id}/scripts` 直接挂载到仿真。
3. 若先上传再挂载，可通过 `POST /simulations/{id}/scripts/attach` 指定脚本。
4. 上传新版本会立即替换旧策略，后续 Tick 自动生效。

> 商业银行角色仅需关注本指南与平台 API 文档，其他角色文档对你不可见。