# 商业银行（Bank）脚本 API

本文档覆盖：
- 平台与脚本的交互契约（入口、sandbox 与返回值约定）
- 脚本可读取的信息（字段列表、格式、经济学含义及推荐取值范围）
- 如何构造决策（返回结构、常见错误与回退策略）
- 支持的决策字段逐项说明（格式、含义、合法范围）

说明与假设：文档基于仓库当前实现和既有文档推断出字段与语义；若实际运行时 `config` 中有更严格约束，以 `config` 为准。若有未指明的范围，本文件会给出合理默认范围建议（并注明为假设）。

## 1. 脚本如何与平台交互（契约）

- 入口函数：必须实现 `def generate_decisions(context)`。
- 传入参数 `context`：一个字典，包含至少这些键：`world_state`, `entity_state`, `config`, `script_api_version`, `agent_kind`, `entity_id`。
- 返回值：推荐使用 `econ_sim.script_engine.user_api.OverridesBuilder()` 构造并返回 `build()` 的结果；也可返回原生 dict，但平台会按白名单和模式校验。
- 出错或选择放弃自定义决策时，可以返回 `None` 或空 dict；平台会在这种情况下使用 baseline（回退）策略。
- 沙箱与限制：脚本运行在受限环境，部分第三方库可能被禁止导入；LLM 调用应使用统一接口（见下）。

## 2. 脚本可以读取的信息（逐字段、经济学含义、格式与合法范围）

注意：所有字段均来自 `context['entity_state']`（针对当前银行）或 `context['world_state']`（宏观/聚合信息）。下面对常见字段逐项解释：

- `id` (str)
    - 含义：银行的唯一标识。
    - 格式：字符串，例如 "BANK-1"。
    - 读写：只读。

- `balance_sheet` (object)
    - 子字段：
        - `cash` (float)：可立即使用的现金。
            - 含义：流动性最强的资产。
            - 格式：非负浮点数。推荐范围：[0, +inf)。
        - `reserves` (float)：在央行的准备金（可用于结算/满足准备金要求）。
            - 格式：非负浮点数。推荐范围：[0, +inf)。
        - `deposits` (float)：客户存款负债（非负）。
        - `loans` (float)：未偿还贷款总额（资产）。
    - 经济学含义：决定银行流动性、资本与放贷能力，是风险与供给决策的基础。

- `deposit_rate` (float)
    - 含义：上一个 tick / 当前记录的存款利率（年化）。
    - 格式：浮点，常以小数表示（例如 0.01 表示 1%）。
    - 合法范围建议：[-0.05, 1.0]（支持负利率但限度由 `config` 决定）；实际以 `config` 为准。

- `loan_rate` (float)
    - 含义：银行对新放贷或标准贷款的报价利率（年化）。
    - 格式：浮点。推荐范围：[0.0, 1.0]。

- `loan_portfolio` / `approved_loans` (dict)
    - 含义：按贷款 id 或借款人分组的贷款明细（本金、期限、违约概率等），用来估算未来现金流与信用风险。
    - 格式：{ loan_id: {"principal": float, "maturity": int, "pd": float, ...}, ... }
    - 常见字段范围：`principal >= 0`, `maturity` 为整数周期数, `pd`（违约概率）在 [0,1]。

- `bond_holdings` (dict)
    - 含义：债券 id -> 持仓数量；影响利率风险与流动性管理。
    - 格式：{bond_id: float}

- `risk_metrics` (object; 可选)
    - 含义：如资本充足率（CAR）、不良贷款率（NPL）等。
    - 常见字段：`capital_ratio` (float in [0,1])、`npl_ratio` (float in [0,1])。若存在，应在下放贷前核查。

- `config`（只读，全局或本实体配置）
    - 含义：系统与世界的约束与参数，例如最大/最小利率、利率步长、资本要求、最大放贷速率等。
    - 格式：字典结构，脚本应优先读取并以之为准。

- `world_state['macro']`（聚合宏观变量）
    - 常见子字段：`inflation` (float)、`gdp_growth` (float)、`policy_rate` (float)、`unemployment` (float)、`bond_yield` (float)
    - 经济学含义：影响银行资产负债定价与市场预期的宏观信号。

（注）若仓库中 `entity_state` 还有其他自定义字段，应以实际序列化内容为准；本文件给出的是主流字段和推荐解释。

## 3. 如何构造决策与决策失效时的处理（fallback）

- 推荐构造：使用 `OverridesBuilder()`，例如：

    from econ_sim.script_engine.user_api import OverridesBuilder
    b = OverridesBuilder()
    b.bank(deposit_rate=..., loan_rate=..., loan_supply=...)
    return b.build()

- 决策校验：平台会对返回的字段做白名单和类型校验。常见失效情形与处理：
    - 脚本抛出异常或返回 `None`：平台采用 baseline（默认）策略。
    - 返回包含未允许的字段：`OverridesBuilder` 会阻止，若绕过则平台拒绝并采用 baseline。
    - 字段类型不匹配或格式错误（例如把字符串返回在数值字段）：平台会尝试解析或直接拒绝该字段；若关键字段缺失/非法，会 fallback 到 baseline 或保留上期值，具体规则以后端实现为准。
    - 值超出 `config` 允许范围：平台可能会 clamp（截断）到合法范围或拒绝该调整并发出告警；脚本应先读取 `config` 并自行 clamp。

- 推荐防护措施（脚本端）：
    - 在脚本内对所有外部输入/LLM 返回做类型解析与范围校验（使用 clamp、min/max）。
    - 捕获所有异常，必要时返回 `None` 或构造安全的回退决策（例如维持上期利率、不增加放贷）。

## 4. 决策字段与逐项说明（格式、经济学含义、合法范围建议）

注意：下面给出的范围为建议值；真实权威约束应来自 `context['config']`。

- `deposit_rate` (float)
    - 含义：建议对客户支付的存款利率（年化）。
    - 格式：浮点（如 0.01 表示 1%）。
    - 建议范围：[-0.05, 1.0]，通常在 [0, 0.2]。

- `loan_rate` (float)
    - 含义：建议对新贷款的标价利率（年化）。
    - 格式：浮点。
    - 建议范围：[0.0, 1.0]，通常在 [0.01, 0.3]。

- `loan_supply` (float)
    - 含义：本 tick 计划新增发放贷款的上限（货币单位）。后端将根据需求和信用筛选实际发放。
    - 格式：非负浮点。
    - 建议范围：[0, cash + reserves + 可用资本 * 某比率]；脚本应使用 `balance_sheet` 与 `risk_metrics` 计算可行上限。

- `deposit_order` / `withdrawal_order` (float)
    - 含义：请求把多少资金从现金转为存款或反向操作（流动性调度）。
    - 格式：可正可负（或分别使用两个字段）；平台会按可用余额与规则执行。

- `liquidity_ops` / `reserve_adjustment`（若平台支持）
    - 含义：如请求向央行借入/存入准备金、进行回购等；需以 `config` 中的 OMO/回购接口格式填写。

## 5. 示例（包含防御性编码与 LLM 使用）

示例：保守放贷策略（带范围校验与回退）

```python
from econ_sim.script_engine.user_api import OverridesBuilder
from econ_sim.utils.llm_session import create_llm_session_from_env

def generate_decisions(context):
        try:
                ent = context.get('entity_state', {}) or {}
                cfg = context.get('config', {}) or {}

                bs = ent.get('balance_sheet', {}) or {}
                cash = float(bs.get('cash', 0.0))
                reserves = float(bs.get('reserves', 0.0))

                # 基于流动性决定新增放贷上限：不超过现金+准备金的一半
                loan_supply = max(0.0, (cash + reserves) * 0.5)

                # 利率建议：保持上期 loan_rate，若 LLM 提供建议则用 clamp 约束
                prev_loan_rate = float(ent.get('loan_rate', 0.05))
                new_loan_rate = prev_loan_rate

                # LLM 仅作建议：严格解析并限制到 allowed_range
                try:
                        session = create_llm_session_from_env()
                        resp = session.generate('建议本 tick loan_rate（只返回数字）', max_tokens=10)
                        text = resp.get('content', '')
                        suggested = float(text.strip().split()[0])
                        # clamp 为示例，真实范围请使用 config
                        suggested = max(-0.05, min(suggested, 1.0))
                        new_loan_rate = suggested
                except Exception:
                        # LLM 失败：保持 prev_loan_rate
                        new_loan_rate = prev_loan_rate

                b = OverridesBuilder()
                b.bank(deposit_rate=float(ent.get('deposit_rate', 0.01)), loan_rate=new_loan_rate, loan_supply=round(loan_supply,2))
                return b.build()
        except Exception:
                # 发生任何不可控错误时，返回 None 触发 baseline 回退
                return None
```

## 6. 额外建议与故障应对流程

- 本地测试：使用仓库内的 tests 或 `dry_run_generate` 快速验证返回结构。
- 日志与监控：脚本中应记录（或返回）关键决策原因以便审计（在允许的范围内）。
- 安全性：不要将敏感凭证硬编码在脚本中；LLM 和外部服务的凭证应由平台环境变量/受控服务提供。

——

若需要，我可以继续把 `docs/user_strategies` 目录下其它主体文档统一为同一风格并加入更多字段的精确取值建议（例如把 `config` 中的具体参数读取并示例 clamp），或者根据你的运行环境把建议范围收敛到 repo 中实际的 `world_settings.yaml` 配置值。

