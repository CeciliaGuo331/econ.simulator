# 平台策略脚本 API 指南

本指南面向所有可上传策略脚本的用户，说明运行时上下文、沙箱约束以及辅助工具模块。阅读完本说明后，再根据自身角色查看对应的策略编写指南。

## 1. 执行环境

- **沙箱隔离**：平台会在独立子进程内执行脚本，并限制 CPU 时间（约 1 秒）与内存（约 256 MB），超限即中止，返回错误。
- **执行超时**：默认 0.75 秒，可由运营方通过环境变量 `ECON_SIM_SCRIPT_TIMEOUT_SECONDS` 调整。脚本需在时限内返回结果。
- **上下文传入**：入口函数必须命名为 `generate_decisions(context)`，其中 `context` 为只含原生 JSON 数据的字典：
  - `context["world_state"]`：当前世界状态快照（数值、列表、字典）。
  - `context["config"]`：世界配置。
  - `context["script_api_version"]`：当前 API 版本号（整数）。
- **返回值**：应返回与 `TickDecisionOverrides` 兼容的字典结构，平台会做类型校验。返回 `None`、空字典或空列表表示不覆盖默认策略。

## 2. 允许的内置函数与模块

- 允许使用的常用内置：`abs`、`all`、`any`、`bool`、`dict`、`enumerate`、`filter`、`float`、`int`、`isinstance`、`issubclass`、`iter`、`len`、`list`、`map`、`max`、`min`、`next`、`object`、`pow`、`print`、`range`、`repr`、`round`、`set`、`sorted`、`str`、`sum`、`tuple`、`type`、`zip` 及常见异常类型。
- 允许导入的模块：`math`、`statistics`、`random`、`econ_sim`、`econ_sim.script_engine`、`econ_sim.script_engine.user_api`。其他模块（如 `os`、`pathlib`、`requests` 等）会被拦截。
- 禁止相对导入、`exec`/`eval`、动态 `__import__`，也无法访问文件系统与网络。

## 3. 用户 API（`econ_sim.script_engine.user_api`）

平台提供了一个轻量级工具模块，脚本可通过

```python
from econ_sim.script_engine.user_api import OverridesBuilder, clamp, fraction, moving_average
```

进行导入，帮助快速构造合法的决策覆盖：

- `OverridesBuilder()`：链式设置各主体的覆盖值，最终通过 `build()` 生成字典。
  - `builder.household(hid, consumption_budget=..., savings_rate=..., labor_supply=...)`
  - `builder.firm(price=..., planned_production=..., wage_offer=..., hiring_demand=...)`
  - `builder.bank(...)`、`builder.government(...)`、`builder.central_bank(...)`
- 常用数值工具：
  - `clamp(value, lower, upper)`：限制数值区间。
  - `fraction(numerator, denominator)`：安全除法，自动规避除零。
  - `moving_average(series, window)`：计算序列的滑动平均，样本不足返回 `None`。

> **注意**：`OverridesBuilder` 仅对合法字段生效，出现未支持字段会直接抛出错误，脚本执行随即失败。

## 4. 编写流程概览

1. 在模板文件 `examples/scripts/strategy_template.py` 基础上实现 `generate_decisions`。
2. 通过平台提供的管理界面或 API 上传脚本，必要时可先上传到个人仓库后再挂载至仿真实例。
3. 挂载成功后，脚本将在每个 Tick 中被执行，生成的覆盖会与默认策略合并，后上传的脚本优先级更高。
4. 如需更新脚本，重新上传即可，版本号会自动刷新，旧代码在下一次 Tick 之前失效。

请继续查阅与你角色对应的策略指南，了解指标解读、默认策略假设与推荐目标。