# 商业银行 (Bank) 脚本 API（面向玩家）

目标读者：为商业银行（bank）编写策略脚本的玩家。本指南与其他主体文档保持风格一致，覆盖可读的 state 字段、可写的决策字段、示例代码、在脚本内使用 LLM 的规范（平台只支持一种方式）以及常见错误和调试建议。

## 重要前置
- 入口函数：你的脚本必须定义 `def generate_decisions(context)`，返回一个决策覆盖（overrides）。
- 推荐构造器：使用 `econ_sim.script_engine.user_api.OverridesBuilder()` 构造返回值（会做字段白名单校验）。
- 隐私与可见性：脚本看到的 `world_state` 会被裁剪成只包含与你相关的信息（bank 脚本只能看到自己的 bank）；不要假设能读到其他玩家的私有数据。

## 一、context 的结构与如何使用
脚本执行时会收到一个 `context` 字典，常用键如下（与 household 文档格式一致）：

- `world_state`：被裁剪的世界快照（dict）——含 `tick`, `day`, `features`, `macro`，以及与你相关的主体子树；用于读取宏观信号与判定是否为 daily decision tick。
- `entity_state`：你的实体的完整序列化状态（等同于 `world_state` 中与你对应的条目），适合直接读取。对 bank 来说即 `BankState` 的序列化结果。
- `config`：世界配置（只读，用于读取利率步长、风控参数等）。
- `script_api_version`：当前脚本 API 版本（int），一般为 1。
- `agent_kind`：字符串，值为 `'bank'`。
- `entity_id`：当前实体 ID（字符串形式）。

下面对 `entity_state` 中常见字段给出详细说明与用法建议：

- `id` (str)
    - 含义：银行唯一标识。用于 `OverridesBuilder.bank(id=...)`。

- `balance_sheet` (object)
    - `cash` (float)：可立即使用的现金。
    - `reserves` (float)：在央行的准备金。
    - `deposits` (float)：客户存款负债。
    - `loans` (float)：未偿贷款总额（资产）。
    - 使用建议：很多决策（利率、放贷供给）应以 `cash + reserves` 与风险暴露为参考。

- `deposit_rate` / `loan_rate` (float)
    - 含义：当前在 state 中记录的利率，可作为上次决策或市场给定的参考。脚本可以在本 tick 提出新的 `deposit_rate` / `loan_rate`。

- `approved_loans` (dict) / `loan_portfolio`
    - 含义：当前已发放或审批中的贷款明细（按借款人或贷款 id 分组）。脚本可读取以估计信用暴露与预期现金流。

- `bond_holdings` (dict)
    - 含义：债券 id -> 持仓数量，影响流动性和利率风险暴露。

- `risk_metrics`（可选）
    - 含义：若世界配置启用，会提供诸如不良贷款率、资本充足率等风控指标；建议在下放贷规模前核查并满足最低阈值。

## 二、可下的决策字段（BankDecision）与字段说明

建议使用 `OverridesBuilder` 提交下述允许的字段：
- `deposit_rate` (float)
    - 本 tick 提议的对客户的存款利率（年化），平台会在结算时根据规则应用或合并。
- `loan_rate` (float)
    - 本 tick 提议的对新放贷款项的标价利率（年化）。
- `loan_supply` (float)
    - 本 tick 计划新增发放的贷款总额上限（货币单位）。后端会根据借款需求与信用筛选实际放贷。
- `deposit_order` / `withdrawal_order` (float)
    - 表示希望把现金与存款间调节的量（同 household 的存/取）；finance 模块会处理实际划拨。

注意事项：
- 平台可能对利率或放贷规模强制施加约束（来自 `config`），脚本应在发出决策前读取 `config` 并做合法性检查。

## 三、如何构造与返回覆盖（推荐）

示例（包含异常保护与字段校验）：

```python
from econ_sim.script_engine.user_api import OverridesBuilder, clamp
from econ_sim.utils.llm_session import create_llm_session_from_env

def generate_decisions(context):
        try:
                ent = context.get('entity_state', {}) or {}
                bid = str(context.get('entity_id'))
                cash = float(ent.get('balance_sheet', {}).get('cash', 0.0))

                # 保守放贷：基于现金与资本比率决定 loan_supply
                loan_supply = round(max(0.0, cash * 0.5), 2)
                # 存贷利差策略（示例）：略微提升 loan_rate
                new_loan_rate = float(ent.get('loan_rate', 0.05)) + 0.005
                new_deposit_rate = clamp(float(ent.get('deposit_rate', 0.01)), 0.0, 0.05)

                b = OverridesBuilder()
                b.bank(deposit_rate=new_deposit_rate, loan_rate=new_loan_rate, loan_supply=loan_supply)
                return b.build()
        except Exception:
                return None
```

## 四、在脚本中使用 LLM（统一的、受支持的方法）

平台对脚本内调用 LLM 的方式只支持一种：

        from econ_sim.utils.llm_session import create_llm_session_from_env

在脚本内使用上面这行来创建会话/客户端，然后调用 `session.generate(prompt, ...)`（或相应 API，视实现而定）。示例：

```python
from econ_sim.utils.llm_session import create_llm_session_from_env
from econ_sim.script_engine.user_api import OverridesBuilder

def generate_decisions(context):
        try:
                session = create_llm_session_from_env()
                prompt = '基于最近的不良贷款率与现金头寸，建议本 tick 的新增放贷比例（0-1），只返回一个数字。'
                resp = session.generate(prompt, max_tokens=20)
                text = resp.get('content', '')
                try:
                        ratio = float(text.strip().split()[0])
                except Exception:
                        ratio = 0.25

                ent = context.get('entity_state', {}) or {}
                cash = float(ent.get('balance_sheet', {}).get('cash', 0.0))
                loan_supply = round(max(0.0, cash * ratio), 2)
                b = OverridesBuilder()
                b.bank(loan_supply=loan_supply)
                return b.build()
        except Exception:
                # 若 LLM 抛错或超时，返回 None 以使用 baseline
                return None
```

注意：
- 一律在代码中对 LLM 返回进行严格解析与数值范围校验（例如 clamp 或上下界限制）；不要信任 LLM 输出的格式性或安全性。
- 若脚本需要大量或复杂的 LLM 交互，建议把 LLM 调用放到外部微服务并把简短结果注入 `world_state`，以减少沙箱内阻塞与超时风险。

## 五、常见错误与排查

- `ScriptExecutionError: 禁止导入模块: 'xxx'`：你尝试导入不在白名单的模块（如 requests）。替代方案：使用 `create_llm_session_from_env` 提供的会话或由管理员开放的受控服务。
- `ScriptSandboxTimeout`：脚本执行超时，缩短逻辑、减少同步阻塞调用或把复杂步骤移到外部服务。
- `ScriptExecutionError: override contains unsupported fields`：返回了不允许覆盖的字段，请核对 `OverridesBuilder` 支持的字段列表。

## 六、本地快速校验模板（dry-run）

在提交脚本前，你可以用一个简单的本地上下文快速校验返回结构：

```python
def dry_run_generate(script_fn, sample_ctx):
        try:
                res = script_fn(sample_ctx)
                print('ok, type=', type(res))
                return True
        except Exception as e:
                print('error', e)
                return False

# sample_ctx 可基于 tests 中的示例构造
```

更多示例与测试模板请参阅 `docs/user_script_api_index.md` 与项目内的 tests 文件夹。

