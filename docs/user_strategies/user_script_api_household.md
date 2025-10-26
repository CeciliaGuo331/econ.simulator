# 家户脚本 API（面向玩家）

目标读者：为家户（household）编写策略脚本的玩家。该文档覆盖你需要了解的世界状态字段、每个字段的含义与建议用法、可下的决策字段、示例与常见错误诊断。

## 重要前置
- 入口函数：你的脚本必须定义 `def generate_decisions(context)`，返回一个决策覆盖（overrides）。
- 推荐构造器：使用 `econ_sim.script_engine.user_api.OverridesBuilder()` 构造返回值（会做字段白名单校验）。
- 隐私与可见性：脚本看到的 `world_state` 会被裁剪成只包含与你相关的信息（household 脚本只能看到自己的 household）；不要假设能读到其他玩家的私有数据。

## 一、context 的结构与如何使用
脚本执行时会收到一个 `context` 字典，常用键如下：

- `world_state`：被裁剪的世界快照（dict）——含 `tick`, `day`, `features`, `macro`，以及与你相关的主体子树；用于读取宏观信号与判定是否为 daily decision tick。
- `entity_state`：你的实体的完整序列化状态（等同于 `world_state` 中与你对应的条目），适合直接读取。对 household 来说即 `HouseholdState` 的序列化结果。
- `config`：世界配置（包含 policies），只读，用于读取折现因子、成本参数等。
- `script_api_version`：当前脚本 API 版本（int），一般为 1。
- `agent_kind`：字符串，值为 `'household'`（本文档针对该类型）。
- `entity_id`：当前实体 ID（字符串形式）。

下面对 `entity_state` 中每一个重要字段给出详细说明、典型取值、脚本内如何使用：

字段列表（详解）

- `id` (int)
  - 含义：家户唯一标识。
  - 使用：在 `OverridesBuilder.household(hid, ...)` 里传入该 id。

- `balance_sheet` (object)
  - `cash` (float)：可立即用于消费或存款的现金。
  - `reserves` (float)：通常与家户无关（可忽略）。
  - `deposits` (float)：在银行的存款，可视作流动性的一部分。
  - `loans` (float)：未偿贷款余额。
  - `inventory_goods` (float)：家户持有的商品数量（若平台允许二级流通）。
  - 使用建议：消费决策通常基于 `cash + deposits` 的流动性估算。

- `skill` (float)
  - 含义：人力资本 / 效率参数，用于匹配与生产计算。
  - 典型区间：0.1–5.0，默认 1.0。
  - 用法：决定是否投资教育（`education_payment`）或接受较低工资岗位。

- `preference` (float)
  - 含义：消费/储蓄偏好，可用于确定边际消费倾向。

- `employment_status` (str enum)
  - 值：`"unemployed"`、`"employed_firm"`、`"employed_government"`。
  - 用法：若为 `unemployed`，通常会希望投更多 labor_supply 或主动找工作（labor_market）。

- `employer_id` (str | None)
  - 含义：当前雇主的 ID（若有）。

- `wage_income` (float)
  - 含义：当前获得的工资收入流量，用于估计可支配收入。

- `labor_supply` (float)
  - 含义：记录家户当前登记的劳动供应量（历史/state 值）；脚本可下新的 `labor_supply` 来表达本 tick 的劳动意愿。
  - 注意：经济引擎 labor_market 会在匹配时检查 `is_studying`（state 或决策中），若为 True 即使脚本返回非零 `labor_supply` 也会被排除。换言之，家户若学习则不会被分配岗位。

- `last_consumption` (float)
  - 含义：goods_market 在清算后写入的**实际成交量**（货物数量），比 `consumption_budget`（计划预算）更可信。
  - 用法：脚本可读取它来估计家户真实消费水平以调整下一期预算。

- `reservation_wage` (float)
  - 含义：家户最低可接受工资。labor_market 会使用该值过滤工资不足的岗位。

- `bond_holdings` (dict)
  - 含义：债券 id -> 持有数量。脚本可据此管理流动性/风险。

- `education_level` (float)
  - 含义：累积教育/技能水平，education 模块会在 daily settlement 根据 `education_payment` 提升该值。
  - 用法：当较低时优先考虑 `is_studying=True` 与 `education_payment`。

- `is_studying` (bool)
  - 含义：家户是否处于学习状态（当前 tick）。重要：该字段会在劳动匹配阶段（labor_market）起到排除作用。

- `lifetime_utility` (float) / `last_instant_utility` (float | None)
  - 含义：后端基于实际消费计算的瞬时与累计折现效用，仅供观察（脚本只读）。脚本应通过调整 `consumption_budget` 等行为间接影响效用，而不能直接写入这些字段。

## 二、可下的决策字段（HouseholdDecision）与字段说明

脚本返回的覆盖应匹配 `HouseholdDecision` 中的字段。下面列出允许写入的字段并说明其语义：

- `labor_supply` (float)
  - 本 tick 希望提供的劳动量（数值解释由仿真设置决定，常用 0/1 表示是否可工作）。labor_market 将以此作为候选池输入；但请注意引擎会忽略 `is_studying` 的家户。

- `consumption_budget` (float)
  - 本 tick 用于购买商品的货币预算（以现金为单位）。goods_market 会以预算及价格决定实际成交量。

- `savings_rate` (float)
  - 要求介于 0.0 与 1.0 之间，表示所得收入中愿意储蓄的比例（由后端处理具体存取逻辑）。

- `is_studying` (bool)
  - 表示家户选择在 daily decision tick 开始学习。仅在 daily tick 有效。若设为 True，应同时设置 `education_payment` 表明支付意愿。

- `education_payment` (float)
  - 本 tick 拟用于教育的支付金额；education 模块会在 daily settlement 扣款并在下一日生效提升 `education_level`。

- `deposit_order` / `withdrawal_order` (float)
  - 表示希望本 tick 将现金 -> 存款 或 存款 -> 现金 的操作量；finance_market 将处理并返回更新。

## 三、如何构造和返回覆盖（推荐）

推荐使用 `OverridesBuilder`：它对字段做白名单校验、便于构造并减少出错。

示例：

```python
from econ_sim.script_engine.user_api import OverridesBuilder

def generate_decisions(context):
    hid = int(context['entity_id'])
    b = OverridesBuilder()
    b.household(hid, consumption_budget=12.5, savings_rate=0.1, labor_supply=1.0)
    return b.build()
```

你也可以直接返回一个与 `TickDecisionOverrides` 兼容的 dict，但要确保字段名与类型完全匹配。

## 四、示例策略（消费 + 学习，含异常保护与日志友好返回）

```python
from econ_sim.script_engine.user_api import OverridesBuilder, clamp

def generate_decisions(context):
    try:
        ent = context.get('entity_state', {}) or {}
        hid = int(context.get('entity_id'))
        cash = float(ent.get('balance_sheet', {}).get('cash', 0.0))
        # 保守消费：现金的 25%
        cons = round(max(0.0, cash * 0.25), 2)
        # 简单储蓄率
        save = clamp(0.1, 0.0, 0.9)

        # 判断是否为 daily decision tick
        features = context.get('world_state', {}).get('features', {}) or {}
        is_daily = bool(features.get('is_daily_decision_tick'))
        is_studying = False
        edu_payment = 0.0
        if is_daily and float(ent.get('education_level', 0.5)) < 0.3:
            is_studying = True
            edu_payment = 2.0

        b = OverridesBuilder()
        b.household(hid, consumption_budget=cons, savings_rate=save, is_studying=is_studying, education_payment=edu_payment)
        return b.build()
    except Exception:
        # 容错：若脚本内部出错，返回 None 以使用 baseline 决策
        return None
```

## 五、在脚本中使用 LLM

在脚本沙箱内访问注入的 `llm` 对象

   - 使用方式：脚本内先检查 `if llm is None`，然后根据 `llm` 的实现调用 `generate` 或 `complete`。
   - 示例：

```python
from econ_sim.utils.llm_session import create_llm_session_from_env

def generate_decisions(context):
  try:
    session = create_llm_session_from_env()
    prompt = '给出消费建议（只返回一个数字表示建议的消费比例，0-1）'
    resp = session.generate(prompt, max_tokens=20)
    text = resp.get('content', '')
    # 解析并回退
    try:
      ratio = float(text.strip().split()[0])
    except Exception:
      ratio = 0.25
    from econ_sim.script_engine.user_api import OverridesBuilder
    ent = context.get('entity_state', {}) or {}
    hid = int(context.get('entity_id'))
    cash = float(ent.get('balance_sheet', {}).get('cash', 0.0))
    cons = max(0.0, cash * ratio)
    b = OverridesBuilder()
    b.household(hid, consumption_budget=round(cons, 2))
    return b.build()
  except Exception:
    return None
```

## 六、常见错误与排查

- `ScriptExecutionError: 禁止导入模块: 'xxx'`：你尝试导入不在白名单的模块（如 requests）。替代方案：使用平台提供的 `/llm/completions` 或管理员启用的 llm 工厂。
- `ScriptSandboxTimeout`：脚本执行超时，缩短逻辑、减少同步阻塞调用或把复杂步骤移到外部服务。
- `ScriptExecutionError: override contains unsupported fields`：返回了不允许覆盖的字段，请核对 `OverridesBuilder` 支持的字段列表。
- `Household script may only override its own household id`：household 脚本不得覆盖其他 household。

## 七、本地快速校验模板

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

更多示例、测试模板与平台端点说明请参阅 `docs/user_script_api_index.md`。