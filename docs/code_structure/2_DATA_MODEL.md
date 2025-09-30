# **数据模型与共享契约 (Data Model & Shared Contract)**

在微服务架构中，服务间通信的数据结构（契约）至关重要。我们将使用 Pydantic 模型来定义这些契约，并将它们放在所有服务都可以引用的 `shared` 代码库中。

## **1. Pydantic 共享数据模型**

这些模型是服务间 API 通信的唯一载体。

**位置: `shared/models/`**

```python
# shared/models/common.py
from pydantic import BaseModel, Field

class Asset(BaseModel):
    cash: float = 0.0
    # ...

class BalanceSheet(BaseModel):
    # ...

# shared/models/agent.py
from .common import BalanceSheet

class AgentState(BaseModel):
    id: int
    balance_sheet: BalanceSheet
    # ...

# shared/models/api.py
from .agent import AgentState

class StateUpdate(BaseModel):
    agent_id: int
    updates: Dict[str, Any] # e.g., {"cash": -50.0, "inventory.item1": -2}

class LogicRequest(BaseModel):
    world_state: WorldState # 包含所有代理人和市场的当前状态

class LogicResponse(BaseModel):
    state_updates: List[StateUpdate]
    logs: List[Dict]
```

**关键设计:**
*   **`LogicRequest` / `LogicResponse`**: 逻辑服务（如市场服务）的 API 将会接收一个包含当前世界状态的 `LogicRequest` 对象，并返回一个 `LogicResponse` 对象。这个响应对象清晰地分离了需要**更新的状态**和需要**记录的日志**，让编排器可以清楚地知道下一步该做什么。
*   **`StateUpdate`**: 这种结构化的更新指令比直接传递新状态更高效，它只包含变化的增量，减少了网络负载，也便于状态服务进行原子操作。

## **2. 状态服务的角色与 Redis 设计**

**状态服务是唯一有权访问 Redis 的组件。** 它将内部的 Redis 实现细节完全封装起来，对外只暴露基于 HTTP 的 CRUD API。

### **Redis 内部实现 (对其他服务不可见)**

状态服务内部依然会使用前一版设计中高效的 Redis 结构：

*   **代理人状态:** 使用 `Redis Hashes`，Key: `sim:{id}:person:{id}`。
*   **决策数据:** 不再需要。决策现在通过 API 直接发送给编排器。
*   **全局状态:** 使用一个简单的 Hash，Key: `sim:{id}:world`，字段包括 `tick`, `day` 等。

### **状态服务的 API (对外暴露)**

*   `GET /state/{sim_id}/full`: 获取完整的世界状态。
*   `GET /state/{sim_id}/agents?ids=1,2,3`: 批量获取指定代理人的状态。
*   `PATCH /state/{sim_id}`: **核心更新接口**。接收一个 `List[StateUpdate]`，并在内部将其转化为一系列 Redis `HINCRBYFLOAT` 或 `HSET` 命令，以事务(Pipeline)方式执行。

**这种封装的好处:**
1.  **强解耦:** 逻辑服务完全不知道数据是存在 Redis 还是 PostgreSQL 里。未来可以无缝切换数据库技术，而无需修改任何逻辑服务。
2.  **安全性与一致性:** 所有状态修改都必须通过状态服务的 API 进行，便于集中管理、校验和执行，防止了不同服务直接操作数据库可能带来的数据不一致问题。
3.  **性能优化:** 状态服务可以在其内部实现缓存、批量操作等优化策略，而调用方无需关心。

## **3. 数据交互流程 (示例: 个人消费)**

1.  **编排器 -> 状态服务:** `GET /state/{sim_id}/full` 获取当前世界状态。
2.  **编排器 -> 市场逻辑服务:** `POST /market/clear_goods`，请求体是一个 `LogicRequest`，包含了上一步获取的世界状态。
3.  **市场逻辑服务 (内部):**
    *   从请求体中解析出所有人的消费决策和企业库存。
    *   执行出清算法。
    *   构建一个 `LogicResponse` 对象，其中 `state_updates` 列表包含类似 `[{"agent_id": 101, "updates": {"cash": -21.0}}, {"agent_id": 201, "updates": {"cash": 21.0, "inventory.item1": -2}}]` 的内容。
    *   返回这个 `LogicResponse` 对象。
4.  **编排器 -> 状态服务:** `PATCH /state/{sim_id}`，请求体就是上一步收到的 `state_updates` 列表。
5.  **状态服务 (内部):**
    *   接收到更新列表。
    *   开启一个 Redis Pipeline。
    *   遍历列表，为每个 update 生成命令：
        *   `HINCRBYFLOAT sim:1:person:101 cash -21.0`
        *   `HINCRBYFLOAT sim:1:firm:201 cash 21.0`
        *   `HINCRBY sim:1:firm:201 inventory:item1 -2`
    *   原子性地执行整个 Pipeline。
    *   返回 `200 OK`。

---
**下一步:**
*   [API接口设计](./3_API_DESIGN.md)
