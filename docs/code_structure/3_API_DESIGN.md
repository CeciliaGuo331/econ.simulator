# **API 接口设计 (微服务)**

本文档定义了构成仿真系统的各个微服务之间的API契约。所有通信都通过HTTP进行。

## **1. API 网关 (API Gateway)**

*   **职责:** 系统的统一入口，对玩家策略可见。
*   **`POST /simulations/{simulation_id}/decisions`**
    *   **功能:** 接收玩家决策。
    *   **转发到:** `编排器服务: POST /run_tick`
    *   **请求 Body:** 玩家提交的决策数据 `List[DecisionIn]`。
    *   **响应:** 直接返回 `编排器服务` 的响应。

## **2. 编排器服务 (Orchestrator Service)**

*   **职责:** 驱动事件循环，调用其他服务。
*   **`POST /run_tick`**
    *   **功能:** 执行一个完整的Tick循环。
    *   **流程:**
        1.  调用 `状态服务` 的 `GET /state` 获取当前世界状态。
        2.  将世界状态和决策数据打包，调用 `市场逻辑服务` 的 `POST /clear_markets`。
        3.  将世界状态和决策数据打包，调用 `代理人逻辑服务` 的 `POST /update_agents`。
        4.  收集所有逻辑服务的返回结果（包含状态更新和日志）。
        5.  调用 `状态服务` 的 `PATCH /state` 来应用所有状态更新。
        6.  调用 `日志服务` 的 `POST /logs` 来记录所有日志。
        7.  返回成功或失败信息。

## **3. 状态服务 (State Service)**

*   **职责:** 唯一管理Redis数据库的服务。
*   **`GET /state/{simulation_id}`**
    *   **功能:** 获取指定仿真的完整世界状态。
    *   **响应 Body:** `WorldState` Pydantic 模型。
*   **`PATCH /state/{simulation_id}`**
    *   **功能:** 批量更新状态。
    *   **请求 Body:** `List[StateUpdate]` 模型。
    *   **实现:** 在内部将更新指令转化为Redis事务并执行。
*   **`GET /state/{simulation_id}/agents`**
    *   **功能:** 批量获取指定的代理人状态。
    *   **查询参数:** `ids=101,102,103`
    *   **响应 Body:** `List[AgentState]`

## **4. 逻辑服务 (示例: 市场逻辑服务)**

*   **职责:** 执行无状态的业务逻辑计算。
*   **`POST /clear_markets`**
    *   **功能:** 计算所有市场的交易结果。
    *   **请求 Body:** `LogicRequest` 模型，包含完整的当前世界状态和相关决策。
    *   **响应 Body:** `LogicResponse` 模型，包含需要应用到状态的 `state_updates` 列表和需要记录的 `logs` 列表。
    *   **关键:** 此服务**不直接修改**任何状态，它只是一个纯粹的计算函数。

## **5. 日志服务 (Logger Service)**

*   **职责:** 接收日志数据并持久化。
*   **`POST /logs/{simulation_id}`**
    *   **功能:** 记录一批日志数据。
    *   **请求 Body:** `List[Dict]`，每个字典是一条日志记录。
    *   **实现:** 在内部缓存日志，并批量写入 Parquet 文件。

---

这个完全基于API的架构确保了每个服务都是一个独立的、可替换的单元。例如，我们可以轻易地用一个基于不同经济学理论的新 `市场逻辑服务` 来替换现有的服务，只要它遵守相同的API契约，整个系统就能无缝运行。这正是“高内聚、低耦合”设计思想的体现。