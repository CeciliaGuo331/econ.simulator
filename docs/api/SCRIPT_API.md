# 用户脚本与共享仿真 API 指南

本文档介绍如何在 `econ.simulator` 中实现“多用户共享同一仿真”以及“通过上传脚本驱动策略”的完整流程。内容涵盖必备的 REST API、请求/响应示例，以及脚本编写规范。

## 1. 多用户共享仿真会话

仿真实例通过 `simulation_id` 唯一标识。任何用户只要指定相同的 `simulation_id`，即可加入同一个仿真会话并共享世界状态。

### 1.1 创建或加入仿真

`POST /simulations`

```json
{
  "simulation_id": "macro-lab",
  "user_id": "alice"
}
```

- 若 `simulation_id` 不存在，则自动初始化世界状态。
- 若已经存在，则复用现有状态并仅登记新用户。返回体中 `current_tick`、`current_day` 始终反映共享进度。

### 1.2 单独登记参与者

`POST /simulations/{simulation_id}/participants`

```json
{
  "user_id": "bob"
}
```

用于在仿真启动后添加新的协作者。接口返回当前参与者完整列表，便于前端展示在线成员。

### 1.3 查询参与者

`GET /simulations/{simulation_id}/participants`

响应示例：

```json
{
  "participants": ["alice", "bob", "carol"]
}
```

## 2. 脚本上传与管理

所有脚本通过 REST API 上传存储，运行时在服务器端沙箱执行，并与内置策略合并。多个脚本可以同时作用于同一仿真。

### 2.1 上传脚本

`POST /simulations/{simulation_id}/scripts`

请求示例：

```json
{
  "user_id": "alice",
  "description": "更激进的消费策略",
  "code": """
from math import tanh

def generate_decisions(context):
    world = context["world_state"]
    macro = world["macro"]
    households = {}
    # 提升一部分家庭的消费预算
    for hid, data in world["households"].items():
        adjustment = max(0.0, tanh(macro["gdp"] / 10000.0) * 50.0)
        households[int(hid)] = {"consumption_budget": data["wage_income"] + adjustment}
    return {"households": households}
"""
}
```

成功时返回：

```json
{
  "script_id": "9c5a6d20-21f4-486a-9f6e-40ce7d9f3f5d",
  "message": "Script registered successfully."
}
```

> 上传脚本的同时会自动登记 `user_id` 为仿真参与者。

### 2.2 列出脚本

`GET /simulations/{simulation_id}/scripts`

```json
{
  "scripts": [
    {
      "script_id": "9c5a6d20-21f4-486a-9f6e-40ce7d9f3f5d",
      "simulation_id": "macro-lab",
      "user_id": "alice",
      "description": "更激进的消费策略",
      "created_at": "2025-10-05T08:32:12.104328+00:00"
    }
  ]
}
```

### 2.3 删除脚本

`DELETE /simulations/{simulation_id}/scripts/{script_id}`

```json
{
  "message": "Script removed."
}
```

## 3. Tick 执行与脚本作用

- `POST /simulations/{simulation_id}/run_tick` 触发一次仿真迭代。
- 系统会**先**加载所有注册脚本，将其输出解析为 `TickDecisionOverrides`，然后再与请求体中的 `decisions` 合并（请求体的覆盖优先级更高）。
- 当脚本或请求均未提供覆盖时，默认采用内置策略。

## 4. 脚本编写规范

脚本必须在顶层定义一个 `generate_decisions(context)` 函数，返回与 `TickDecisionOverrides` 兼容的字典。`context` 提供以下字段：

| 键名            | 含义                                               |
| --------------- | -------------------------------------------------- |
| `world_state`   | 当前世界状态的 JSON 结构（数值类型均为 `float`）。 |
| `config`        | 世界配置的 JSON 结构。                             |

返回值示例：

```python
def generate_decisions(context):
    return {
        "households": {
            0: {"consumption_budget": 320.0, "savings_rate": 0.15}
        },
        "firm": {"price": 11.0},
        "government": {"tax_rate": 0.18}
    }
```

**注意事项：**

1. 仅允许使用有限的内建函数（`abs`、`min`、`max`、`sum`、`len`、`sorted`、`round`、`enumerate`、`range`）。
2. 不支持导入外部模块，也无法执行磁盘或网络 IO。
3. 若脚本抛出异常或返回数据结构无效，整次 Tick 将报错并中止，请在上传前充分测试。

## 5. 示例脚本

`examples/scripts/sample_strategy.py` 提供了一个完整模板，展示如何根据宏观数据调整各主体决策，并在本地先行验证。建议用户按照该示例框架扩展自定义策略。

---
通过上述接口与规范，即可实现“多个用户共享同一套仿真，并通过上传脚本驱动策略”的核心流程。前端可结合参与者列表、脚本库与实时 tick 结果，构建协作式的宏观经济策略实验环境。