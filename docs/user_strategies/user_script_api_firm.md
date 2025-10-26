# 企业 (Firm) 脚本 API（面向玩家）

目标读者：为企业（firm）编写策略脚本的玩家。该文档与 household 文档保持一致的章节结构，覆盖 context 结构、字段含义、可写决策、示例、LLM 使用规范及排错建议。

## 重要前置
- 入口函数：你的脚本必须定义 `def generate_decisions(context)`，返回一个决策覆盖（overrides）。
- 推荐构造器：使用 `econ_sim.script_engine.user_api.OverridesBuilder()` 构造返回值（会做字段白名单校验）。
- 隐私与可见性：脚本看到的 `world_state` 会被裁剪，只包含与你相关的信息；不要假设能读到其他玩家的私有数据。

## 一、context 的结构与如何使用

- `world_state`：被裁剪的世界快照（含 `tick`, `day`, `features`, `macro`），以及和你有关的市场/主体子树。
- `entity_state`：你的实体的完整序列化状态（FirmState）。
- `config`：世界配置（只读，用于读取步长、上限等约束）。
- `script_api_version`、`agent_kind`（值为 `'firm'`）、`entity_id`。

下面对 `entity_state` 中的重要字段给出详细说明：

- `id` (str)
  - 含义：企业唯一标识。

- `balance_sheet` (dict)
  - `cash` (float)：可支配现金，用于支付工资、购买中间品和投资。
  - `inventory_goods` (float)：库存商品数量，影响可售量与价格决策。

- `price` (float)
  - 含义：商品的当前标价，goods_market 会以价格与库存与需求撮合成交。

- `wage_offer` (float)
  - 含义：面向 labor_market 的工资报价，用于吸引合适劳动力。

- `planned_production` (float)
  - 含义：本 tick 希望生产的目标产量；后端生产模块会基于资本、劳动与技术将计划转为实际产出。

- `productivity` (float)
  - 含义：平均每单位劳动或资本的产出效率。

- `employees` (list[int])
  - 当前雇佣的家户 id 列表，脚本可读取以判断是否需补招。

- `last_sales` (float)
  - 含义：goods_market 在上一个 tick 写入的实际成交量，通常比计划更可信。

## 二、可下决策字段（FirmDecision）与说明

允许写入的字段（示例）——请使用 `OverridesBuilder.firm(...)`：

- `price` (float)
  - 本 tick 新的商品标价。
- `planned_production` (float)
  - 目标产量。
- `wage_offer` (float)
  - 本 tick 对招工的工资报价。
- `hiring_demand` (int)
  - 希望新增招聘的岗位数。

注意：实际产出由后端生产模块决定；`planned_production` 只是你的计划输入。

## 三、如何构造与返回覆盖（示例与容错）

```python
from econ_sim.script_engine.user_api import OverridesBuilder
from econ_sim.utils.llm_session import create_llm_session_from_env

def generate_decisions(context):
    try:
        ent = context.get('entity_state', {}) or {}
        price = max(0.1, float(ent.get('price', 10.0)) * 0.98)
        planned = max(0.0, float(ent.get('planned_production', 0.0)) + 10.0)
        wage = float(ent.get('wage_offer', 80.0))

        b = OverridesBuilder()
        b.firm(price=round(price,2), planned_production=planned, wage_offer=wage, hiring_demand=3)
        return b.build()
    except Exception:
        return None
```

示例说明：对输入做了类型保护与下界保护，以避免脚本异常导致整个决策失败。

## 四、与劳动力市场的交互要点

- `hiring_demand` 与 `wage_offer` 会直接影响 labor_market 的匹配结果；提高 `wage_offer` 可以放宽候选人筛选并提高匹配概率。
- labor_market 会排除 `is_studying == True` 的家户（无论该标记来自 state 还是决策）。

## 五、在脚本中使用 LLM（统一、受支持的方法）

平台对脚本内调用 LLM 的方式只支持一种：

    from econ_sim.utils.llm_session import create_llm_session_from_env

示例（从 LLM 获取定价建议并严格解析）：

```python
from econ_sim.utils.llm_session import create_llm_session_from_env
from econ_sim.script_engine.user_api import OverridesBuilder

def generate_decisions(context):
    try:
        session = create_llm_session_from_env()
        prompt = '基于最近 10 期的 last_sales 与库存，建议一个价格调整比例（0-1，只返回数字）'
        resp = session.generate(prompt, max_tokens=20)
        text = resp.get('content', '')
        try:
            ratio = float(text.strip().split()[0])
        except Exception:
            ratio = 0.02

        ent = context.get('entity_state', {}) or {}
        curr = float(ent.get('price', 10.0))
        new_price = max(0.1, curr * (1 + ratio))
        b = OverridesBuilder()
        b.firm(price=round(new_price,2))
        return b.build()
    except Exception:
        return None
```

注意：对 LLM 输出进行范围检查与回退；不要直接信任文本格式，防止解析异常。

## 六、常见问题与排查

- 产能与销量差距：若 `last_sales` << `planned_production`，请检查 `employees`、`wage_offer` 与 `price`；后端生产模块也可能受资本或原料约束。
- 招工失败：当 `hiring_demand` 没有招到足够员工时，先确认 `wage_offer` 是否充分以及候选池的 `reservation_wage` 分布。
- 脚本抛异常或返回 None：平台将回退到 baseline 策略；请在脚本中添加异常保护并在本地做 dry-run 测试。

## 七、本地 dry-run 模板

参考 household 文档中的 `dry_run_generate` 示例，在本地构造 sample_ctx（可参考 tests 中的 fixture）以验证返回结构。
