
# 央行 (Central Bank) 脚本 API（面向玩家）

目标读者：为央行（central_bank）编写策略脚本的玩家。本文件和其他主体文档保持统一结构：context 说明、可写决策、示例、LLM 使用规范（仅支持一种调用方式）、常见错误与调试建议。

## 重要前置
- 入口函数：你的脚本必须定义 `def generate_decisions(context)` 并返回决策覆盖（overrides）。
- 推荐使用 `econ_sim.script_engine.user_api.OverridesBuilder()` 构造返回值。
- 央行脚本看到的 `world_state` 只包含允许可见的宏观数据与本体状态，请不要依赖未公开的私有信息。

## 一、context 的结构与如何使用

- `world_state`：包含 `tick`, `day`, `features`, `macro`，以及你可以看到的市场/机构快照（例如 market rates、inflation、unemployment）。
- `entity_state`：当前央行的序列化状态，典型字段如下。
- `config`：系统与政策配置（如最小/最大 policy_rate 步长等）。
- `script_api_version`、`agent_kind`、`entity_id` 同 household 文档。

常见 `entity_state` 字段说明：

- `id` (str)
- `balance_sheet` (dict)
- `policy_rate` / `base_rate` (float)：当前记录的政策利率参考值。
- `reserve_ratio` (float)：央行对商业银行准备金率的设定。
- `inflation_target` / `unemployment_target` (float)
- `bond_holdings` (dict)
- `omo_history`（可选）：近期公开市场操作记录，便于判断市场流动性变化。

## 二、可下的决策字段（CentralBankDecision）与字段说明

使用 `OverridesBuilder.central_bank(...)` 提交下列字段：

- `policy_rate` (float)
  - 建议的政策利率（年化），会被平台合并与检查合法性（例如步长/上下限）。
- `reserve_ratio` (float)
  - 对商业银行施加的准备金要求比例。
- `omo_ops` (list)
  - 一组公开市场操作的建议，每项为：{"bond_id": str, "side": "buy"|"sell", "quantity": float, "price": float}。平台会把这些建议提交到 OMO 执行模块并按市场规则处理。

注意：policy 变动会有滞后且对宏观变量影响显著，脚本应考虑目标（`inflation_target`/`unemployment_target`）和滞后效应。

## 三、如何构造与返回覆盖（示例）

```python
from econ_sim.script_engine.user_api import OverridesBuilder
from econ_sim.utils.llm_session import create_llm_session_from_env

def generate_decisions(context):
    try:
        ent = context.get('entity_state', {}) or {}
        # 简单示例：当通胀高于目标时小幅加息
        inflation = float(context.get('world_state', {}).get('macro', {}).get('inflation', 0.0))
        target = float(ent.get('inflation_target', 0.02))
        policy_rate = float(ent.get('policy_rate', 0.01))
        if inflation > target + 0.005:
            policy_rate = round(policy_rate + 0.002, 4)
        elif inflation < target - 0.005:
            policy_rate = round(max(0.0, policy_rate - 0.002), 4)

        b = OverridesBuilder()
        b.central_bank(policy_rate=policy_rate)
        return b.build()
    except Exception:
        return None
```

## 四、在脚本中使用 LLM（统一、受支持的方法）

平台对脚本内调用 LLM 的方式只支持一种：

    from econ_sim.utils.llm_session import create_llm_session_from_env

使用示例：

```python
from econ_sim.utils.llm_session import create_llm_session_from_env
from econ_sim.script_engine.user_api import OverridesBuilder

def generate_decisions(context):
    try:
        session = create_llm_session_from_env()
        prompt = '请基于最近 12 期的通胀与失业率数据，建议 policy_rate 调整（只返回数字）'
        resp = session.generate(prompt, max_tokens=30)
        text = resp.get('content', '')
        try:
            suggested = float(text.strip().split()[0])
        except Exception:
            suggested = float(context.get('entity_state', {}).get('policy_rate', 0.01))

        b = OverridesBuilder()
        b.central_bank(policy_rate=suggested)
        return b.build()
    except Exception:
        return None
```

注意：央行决策影响面广，务必在脚本中对 LLM 输出做范围与步长校验（例如参考 `config` 中的限制），并在解析失败时使用稳健的回退值。

## 五、常见错误与排查

- `ScriptExecutionError: override contains unsupported fields`：不要提交未列出的字段，使用 `OverridesBuilder` 会降低出错概率。
- `ScriptSandboxTimeout`：LLM 调用或复杂计算可能导致超时，保持 prompt 与解析逻辑简短。
- OMO 操作未被执行：检查 `omo_ops` 字段格式、数量和价格约束是否满足系统要求。

## 六、dry-run 与本地调试

同 household 文档中描述的 `dry_run_generate` 模板，可用来在本地构造 `sample_ctx` 并验证 `generate_decisions` 的返回结构是否符合平台预期。


