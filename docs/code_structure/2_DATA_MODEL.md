# **数据模型与访问层设计**

在模块化单体架构中，数据模型和数据访问被清晰地分离到专有的层级，以确保逻辑代码的纯粹性。

## **1. Pydantic 数据模型**

所有核心业务实体都由 Pydantic 模型定义，它们是模块间函数调用的“契约”。

**位置: `econ_sim/data_access/models.py`**

```python
# econ_sim/data_access/models.py
from pydantic import BaseModel, Field
from typing import Dict, List, Any

class Asset(BaseModel):
    cash: float = 0.0
    # ... 其他资产

class BalanceSheet(BaseModel):
    assets: Asset
    # ...

class AgentState(BaseModel):
    id: int
    balance_sheet: BalanceSheet
    # ... 其他状态

class WorldState(BaseModel):
    """
    一个 Tick 开始时，世界状态的完整快照。
    这是从数据访问层传递给核心编排器的主要数据结构。
    """
    tick: int
    day: int
    agents: Dict[int, AgentState]
    # ... 其他全局市场状态

class StateUpdateCommand(BaseModel):
    """
    一个结构化的更新指令。
    逻辑模块计算完毕后，返回此对象的列表，由编排器交给数据访问层执行。
    """
    agent_id: int
    updates: Dict[str, Any] # e.g., {"cash": -50.0, "inventory.item1": -2}

```

**关键设计:**
*   **`WorldState`**: 这是一个只读的（By Convention）数据快照。编排器在每个 Tick 开始时从数据访问层获取它，然后将其作为参数传递给各个纯函数式的逻辑模块。
*   **`StateUpdateCommand`**: 逻辑模块的计算结果不是一个新的 `WorldState`，而是一个描述“变化”的指令列表。这种方式更清晰、高效，也避免了直接修改状态带来的副作用。

## **2. 数据访问层 (`data_access`)**

**`data_access` 模块是唯一有权访问 Redis 的组件。** 它将内部的 Redis 实现细节完全封装起来，对外只暴露基于 Pydantic 模型的异步函数接口。

### **Redis 内部实现 (对其他模块不可见)**

`data_access` 内部依然会使用高效的 Redis 结构：

*   **代理人状态:** 使用 `Redis Hashes`，Key: `sim:{id}:person:{id}`。
*   **全局状态:** 使用一个简单的 Hash，Key: `sim:{id}:world`，字段包括 `tick`, `day` 等。

### **`redis_client.py` 的函数接口 (对外暴露)**

```python
# econ_sim/data_access/redis_client.py
from .models import WorldState, StateUpdateCommand

async def get_world_state(sim_id: int) -> WorldState:
    """
    从 Redis 读取数据，组装成一个完整的 WorldState Pydantic 模型。
    """
    # ... 实现代码: 使用 aio-redis 的 pipeline 获取所有相关 hash ...
    pass

async def apply_updates(sim_id: int, updates: List[StateUpdateCommand]):
    """
    将状态更新指令列表转化为一系列 Redis HINCRBYFLOAT/HSET 命令，
    并以事务(Pipeline)方式原子性地执行。
    """
    # ... 实现代码: 遍历列表，生成 redis 命令并执行 ...
    pass
```

**这种封装的好处:**
1.  **强解耦:** 逻辑模块 (`logic_modules`) 和编排模块 (`core`) 完全不知道数据是存在 Redis 还是其他数据库里。未来可以无缝切换数据库技术，而无需修改任何业务逻辑。
2.  **一致性与原子性:** 所有状态修改都必须通过 `apply_updates` 函数进行，它保证了所有更新在一个事务中完成，避免了数据不一致的风险。
3.  **性能优化:** `data_access` 模块可以在其内部实现缓存、批量操作等优化策略，而调用方无需关心。

## **3. 数据交互流程 (示例: 个人消费)**

1.  **编排器 -> 数据访问层:**
    ```python
    # core/orchestrator.py
    current_state: WorldState = await data_access.get_world_state(sim_id=1)
    ```
2.  **编排器 -> 逻辑模块:**
    ```python
    # core/orchestrator.py
    update_commands, logs = logic_modules.market_logic.clear_goods(
        world_state=current_state
    )
    ```
3.  **逻辑模块 (内部):**
    *   `clear_goods` 是一个纯函数，它接收 `WorldState`。
    *   执行出清算法。
    *   构建并返回一个 `List[StateUpdateCommand]` 和日志。例如：
        `[StateUpdateCommand(agent_id=101, updates={"cash": -21.0}), ...]`
4.  **编排器 -> 数据访问层:**
    ```python
    # core/orchestrator.py
    if update_commands:
        await data_access.apply_updates(sim_id=1, updates=update_commands)
    ```
5.  **数据访问层 (内部):**
    *   `apply_updates` 接收到指令列表。
    *   开启一个 Redis Pipeline。
    *   遍历列表，为每个 update 生成命令：
        *   `HINCRBYFLOAT sim:1:person:101 cash -21.0`
        *   ...
    *   原子性地执行整个 Pipeline。

整个流程都在一个应用进程内完成，通过异步函数调用连接，没有网络开销，同时保持了清晰的职责分离。

---
**下一步:**
*   [API接口设计](./3_API_DESIGN.md)
