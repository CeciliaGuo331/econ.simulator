# **API 接口设计 (模块化单体)**

在模块化单体架构中，内部模块间的通信通过直接的函数调用完成，无需定义内部 API。因此，本文档只定义系统**对外暴露**的 HTTP API，供玩家策略、外部监控工具等客户端使用。

所有 API 都由 `econ_sim/api/endpoints.py` 中的 FastAPI 路由实现。

## **1. 核心设计理念**

*   **单一入口:** 所有外部交互都通过这套统一的 RESTful API 进行。
*   **无状态:** API 自身是无状态的，每次请求都包含了完整执行所需的信息。
*   **异步处理:** 对于可能耗时较长的操作（如运行一个完整的 Tick），API 将采用异步模式，立即返回一个任务 ID，客户端可以通过该 ID 查询任务状态。

## **2. API 端点 (Endpoints)**

### **仿真管理 (Simulation Management)**

*   **`POST /simulations`**
    *   **功能:** 创建一个新的仿真实例。
    *   **请求 Body:** 包含仿真配置的 JSON 对象 (例如，指定要使用的 `config/world_settings.yaml` 文件)。
    *   **成功响应 (201 Created):**
        ```json
        {
          "simulation_id": "sim_a1b2c3d4",
          "message": "Simulation created successfully."
        }
        ```

*   **`GET /simulations/{simulation_id}`**
    *   **功能:** 获取指定仿真的高级状态（如 Tick, Day, 运行状态）。
    *   **成功响应 (200 OK):**
        ```json
        {
          "simulation_id": "sim_a1b2c3d4",
          "status": "paused",
          "current_tick": 15,
          "current_day": 5
        }
        ```

### **仿真控制 (Simulation Control)**

*   **`POST /simulations/{simulation_id}/run_tick`**
    *   **功能:** 触发并执行下一个 Tick。这是一个核心操作。
    *   **请求 Body:**
        ```json
        {
          "decisions": [
            { "agent_id": 101, "action": "consume", "params": { ... } },
            { "agent_id": 201, "action": "produce", "params": { ... } }
          ]
        }
        ```
    *   **成功响应 (200 OK):**
        ```json
        {
          "message": "Tick execution completed.",
          "new_tick": 16,
          "new_day": 5,
          "logs": [ ... ] // (可选) 返回本次 Tick 的关键日志
        }
        ```
    *   **实现说明:** 这个 API 端点会直接调用 `core.orchestrator` 中的主函数来执行 Tick 循环。由于是单体应用，这个调用是直接、高效的。

### **数据查询 (Data Query)**

*   **`GET /simulations/{simulation_id}/state/full`**
    *   **功能:** 获取当前完整的世界状态快照。**注意:** 这可能是一个非常大的响应，主要用于调试或特定的分析客户端。
    *   **成功响应 (200 OK):** 返回 `WorldState` Pydantic 模型序列化后的 JSON。

*   **`GET /simulations/{simulation_id}/state/agents`**
    *   **功能:** 批量获取指定代理人的状态。
    *   **查询参数:** `ids=101,102,103`
    *   **成功响应 (200 OK):** 返回 `List[AgentState]` 序列化后的 JSON 数组。

---

这个简化的 API 设计反映了模块化单体架构的优势：内部复杂性通过代码结构和函数调用来管理，对外则提供一个干净、一致且易于理解的接口。所有内部服务间的网络开销和序列化/反序列化成本都已消除。