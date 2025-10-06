# 政府策略编写指南

本指南为“政府”用户提供编写财政政策脚本的参考。脚本会覆盖默认政府策略，仅政府角色可见。

## 1. 策略目标

默认政府策略（`BaseGovernmentStrategy`）主要做以下决策：

- 将当前税率向配置中的目标税率平滑收敛。
- 根据失业率缺口新增政府就业岗位。
- 设定失业补贴预算与家庭转移支出。

你可以扩展的方向：

- 动态财政刺激：结合 GDP 增速、税基变化调整税率与支出。
- 公共就业策略：设置不同岗位上限或优先雇佣特定群体。
- 协同社保：与商业银行或企业策略联动，缓冲经济周期。

## 2. 平台 API 摘要

- 脚本入口：`generate_decisions(context)`。
- 使用 `OverridesBuilder.government(...)` 可设定 `tax_rate`、`government_jobs`、`transfer_budget`。
- 可从 `context["world_state"]["government"]` 获取财政状态，`context["world_state"]["macro"]` 获取宏观指标。

## 3. 策略代码模板

```python
from typing import Any, Dict
from econ_sim.script_engine.user_api import OverridesBuilder, clamp

def generate_decisions(context: Dict[str, Any]) -> Dict[str, Any]:
    world = context["world_state"]
    macro = world["macro"]
    government = world["government"]

    unemployment_gap = max(0.0, macro["unemployment_rate"] - 0.06)
    budget_base = government["revenue"] - government["expenditure"]

    builder = OverridesBuilder()
    builder.government(
        tax_rate=clamp(government["tax_rate"] - unemployment_gap * 0.3, 0.05, 0.5),
        government_jobs=int(government["government_jobs"] + unemployment_gap * 200),
        transfer_budget=max(0.0, budget_base * 0.5 + unemployment_gap * 1_000_000),
    )
    return builder.build()
```

## 4. 提交步骤

1. 使用模板编写脚本并自测逻辑。
2. 上传：
   - `POST /scripts` → 保存到个人脚本库。
   - `POST /simulations/{id}/scripts` → 直接挂载到仿真。
3. `POST /simulations/{id}/scripts/attach` 可将个人库脚本绑定到指定仿真实例。
4. 上传新的脚本版本即可替换旧策略，无需额外下线操作。

> 政府用户无需查看其他角色的文档。