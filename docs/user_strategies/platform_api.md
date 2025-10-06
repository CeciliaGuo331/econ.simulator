# 平台策略脚本 API 指南

本指南帮助你理解平台如何执行策略脚本、`context` 中包含哪些数据、以及如何利用辅助工具生成合法的决策结果。阅读完毕后，你可以结合各角色的专属指南（家户、企业、政府等）继续深化脚本设计。

## 1. 脚本在平台上的运行方式

- **固定入口函数**：每个脚本都需要定义 `generate_decisions(context)`。平台会在每个 Tick 调用该函数，并把当前世界信息打包进 `context` 字典。
- **沙箱隔离**：脚本在独立的子进程中执行，与平台主程序隔离。系统为每次执行分配约 1 秒 CPU 时间和 256 MB 内存；如果耗时或耗内存超出限制，脚本会被立即终止，本次 Tick 视为失败。
- **执行时限**：默认超时为 0.75 秒，可通过环境变量 `ECON_SIM_SCRIPT_TIMEOUT_SECONDS` 调整。请确保所有计算（含复杂统计或循环）都在时限内完成。
- **出错反馈**：若代码抛出异常或返回不符合要求的数据结构，平台会记录错误原因。你可以在脚本上传界面或日志中查看失败详情并修正。

## 2. `context` 字典里有什么？

`context` 仅包含基础的 Python 数据类型（数字、字符串、列表、字典），便于预测和调试。最常用的键如下：

| 键名 | 内容说明 | 常见用途 |
| ---- | -------- | -------- |
| `context["world_state"]` | 针对当前 Tick 的只读快照。它聚合了与你角色相关的私有状态、匿名化的市场信息以及与决策紧密相关的宏观指标。 | 读取经济状态、判断是否需要调整策略。 |
| `context["config"]` | 世界配置，例如 Tick 时长、默认政策参数、约束阈值。 | 校准策略参数或判定边界值。 |
| `context["script_api_version"]` | 脚本 API 版本号（整数），用于兼容性检查。 | 确认所使用的字段是否在当前版本中受支持。 |

> ⚠️ **可见性约束**：`world_state` 为了兼顾性能仍使用统一的 JSON 结构，但平台只保证下表所列字段的可用性。其他字段可能用于调试或未来扩展，随时可能移除。请仅依赖文档中声明的键，否则脚本可能在后续版本失效。

### 2.1 角色视角下的可见数据

| 角色 | 私有 `agent_state`（保证可见） | 公共 `market_data`（保证可见） | 无法访问的内容 |
| ---- | ---------------------------- | ------------------------------ | -------------- |
| 家户 | 个人现金、存款、债务、过去几期工资与消费、当前就业状态。 | 商品价格、可申请岗位数量与工资均值、存贷款利率、宏观指标（GDP、通胀、失业率）。 | 其他家户的详细预算、企业/政府内部账簿、银行风险指标。 |
| 企业 | 现金、存货、生产率、员工名单、待处理订单。 | 家户总需求、公开工资、贷款利率、宏观指标。 | 单个家户的现金或偏好、银行内部风险模型、政府预算细项。 |
| 政府 | 税基、财政收入/支出、在岗人数、债务余额。 | 公开失业率、工资水平、基准利率、国债收益率。 | 单个企业的详细成本、家庭资产、银行流动性细节。 |
| 商业银行 | 资产负债表、贷款组合、准备金、违约率。 | 央行政策利率、政府国债发行计划、市场平均工资。 | 个人信用评分细节（脱敏后仅给定高层指标）、企业具体存货。 |
| 央行 | 政策工具当前取值、目标区间、全局信贷供给。 | GDP、通胀、失业率、贷款增速、金融压力指数。 | 家户/企业的私有账簿、单笔贷款合同。 |

`world_state` 中的键会按照以上划分存放在类似 `world_state["households"][<你的ID>]`、`world_state["macro"]` 的层次结构中。若你发现同一结构里还包含其他调试字段，请忽略它们：这些字段不在 API 合约中，未来可能被移除或重命名。

### 2.2 调试建议

你可以在本地运行脚本并打印允许的字段来确认其结构：

```python
def generate_decisions(context):
    my_state = context["world_state"]["households"].get("123")
    print("家户 123 的状态", my_state)
    print("公开宏观指标", context["world_state"]["macro"])
    return {}
```

平台会把 `print` 输出写入日志（可在仪表盘的“下载最近日志”按钮获取），便于你快速了解经济主体的字段。部署到正式环境前，请移除或注释掉这些调试语句，以免刷屏。

## 3. 如何返回合法的决策结果

平台期望 `generate_decisions` 返回一个与 `TickDecisionOverrides` 结构一致的字典。最简单的方式是使用内置的 `OverridesBuilder`：

```python
from econ_sim.script_engine.user_api import OverridesBuilder

def generate_decisions(context):
    builder = OverridesBuilder()
    builder.firm(price=95.0, planned_production=120.0)
    return builder.build()
```

`builder.build()` 会生成平台需要的标准结构。如果你返回 `None`、空字典或空列表，平台会理解为“保持默认策略，不做覆盖”。

## 4. 可用的工具与模块

- **常用内置函数**：`abs`、`sum`、`len`、`max`、`min`、`sorted`、`range`、`round` 等函数均可使用，适合进行基础计算。
- **允许导入的模块**：`math`、`statistics`、`random`、以及平台提供的 `econ_sim` 系列模块。涉及文件、网络或系统调用的模块（例如 `os`、`pathlib`、`requests`）会被拦截。
- **类型注解提示**：脚本运行环境不允许导入 `typing` 或 `__future__`，如需类型提示可直接使用内置泛型语法（例如 `dict[str, object]`）。
- **辅助工具函数**（位于 `econ_sim.script_engine.user_api`）：
  - `clamp(value, lower, upper)`：把数值压在指定区间内。
  - `fraction(numerator, denominator)`：安全地做除法，如果分母接近零会返回 `0.0`。
  - `moving_average(series, window)`：计算滑动平均；当历史数据不足时返回 `None`。
- **安全限制**：禁用 `exec`、`eval`、相对导入以及动态 `__import__`。脚本无法读写文件，也不能发起网络请求。

## 5. 五步完成第一个策略脚本

1. **复制模板**：在仓库中找到 `examples/scripts/strategy_template.py`，将其复制到你的项目或直接在平台界面粘贴。
2. **观察现状**：在 `generate_decisions` 中读取 `context["world_state"]`。先打印关键数据，理解当前经济位置。例如企业库存、失业率等。
3. **设定判断规则**：决定你要关注的指标（如通胀、消费支出、就业），并编写简单的 if/else 或数值公式。
4. **用 `OverridesBuilder` 输出结果**：针对对应主体调用 `builder.household(...)`、`builder.firm(...)`、`builder.government(...)` 等方法。每个方法都接受命名参数，未设置的字段保持默认值。
5. **上传与测试**：通过管理界面或 API 上传脚本。若想先保存草稿，可上传到个人仓库，确认无误后再挂载到具体仿真实例。

## 6. 示例：企业根据库存调整生产计划

下面的例子改写自平台的基线企业策略，演示如何将经济学直觉转化为代码：

```python
from econ_sim.script_engine.user_api import OverridesBuilder, clamp

def generate_decisions(context):
    world = context["world_state"]
    firm = world["firm"]
    households = world["households"]
    macro = world["macro"]

    # 估计需求：取家户人数 × 60，或者最近消费的 80%，取较大的那个
    household_count = max(1, len(households))
    recent_consumption = sum(h.get("last_consumption", 0.0) for h in households.values())
    demand_proxy = max(household_count * 60.0, recent_consumption * 0.8)

    # 根据库存缺口安排生产
    inventory = firm["balance_sheet"].get("inventory_goods", 0.0)
    desired_inventory = household_count * 1.5
    inventory_gap = desired_inventory - inventory
    planned_production = max(0.0, demand_proxy * 0.5 + inventory_gap)

    # 根据库存和失业率微调价格与工资
    price_factor = clamp(1.0 + inventory_gap / max(desired_inventory, 1.0) * 0.1, 0.9, 1.1)
    wage_factor = clamp(1.0 - macro.get("unemployment_rate", 0.0) * 0.1, 0.9, 1.1)

    builder = OverridesBuilder()
    builder.firm(
        planned_production=round(planned_production, 2),
        price=round(firm["price"] * price_factor, 2),
        wage_offer=round(firm.get("wage_offer", 80.0) * wage_factor, 2),
    )
    return builder.build()
```

这段代码读取库存、消费、失业率等经济变量，再用 `clamp` 控制调整幅度，最终由 `OverridesBuilder` 输出平台认可的结构。你可以把它改成关注其他指标（例如预期通胀、贷款利率），思路相同。

## 7. 常见问题与排查

- **脚本没有生效**：确认函数名为 `generate_decisions` 且返回值不为 `None`。查看上传日志是否有“字段不支持”或“类型错误”等提示。
- **计算结果出现极端值**：使用 `clamp`、`fraction` 控制数值范围，防止异常输入导致过大或过小的输出。
- **运行超时**：检查是否在循环中遍历了大量历史数据，或使用了复杂统计。可提前筛选数据或降低窗口长度。
- **多人协作**：后上传的脚本优先级更高，同一个主体的多个脚本会按上传时间覆盖。需要合并策略时，请将逻辑写在同一个脚本内。

完成本篇后，建议继续阅读你的角色专属指南，了解默认行为、关键指标与常见策略模板，从而编写更符合经济直觉的脚本。