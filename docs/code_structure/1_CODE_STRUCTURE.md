# **代码结构设计 (微服务)**

本设计将系统拆分为多个独立的微服务，每个服务都有自己专属的目录和代码库，以实现最大限度的解耦。

## **1. 根目录结构 (Monorepo 风格)**

我们将采用 Monorepo（单一代码库）风格来管理所有微服务，便于统一管理依赖和共享配置。

```
econ.simulator/
├── .venv/                  # 共享的 Python 虚拟环境
├── configs/                # 所有服务的共享配置文件
├── data/                   # 输出的 Parquet 日志数据
├── docs/                   # 项目文档
├── services/               # 所有微服务的源代码
│   ├── api_gateway/        # API 网关服务
│   ├── orchestrator/       # 编排器服务
│   ├── state_service/      # 状态服务
│   ├── market_logic_service/ # 市场逻辑服务
│   └── ...                 # 其他逻辑和日志服务
├── shared/                 # 跨服务共享的 Pydantic 模型
├── scripts/                # 辅助脚本 (如启动所有服务)
├── tests/                  # 测试代码
├── docker-compose.yml      # 用于启动所有服务的 Docker Compose 文件
├── .gitignore
├── pyproject.toml          # 项目元数据和依赖管理
└── README.md
```

## **2. `shared/` 共享代码包**

为了确保服务间 API 契约的一致性，所有 Pydantic 数据模型将放在一个共享包中。

```
shared/
└── models/
    ├── __init__.py
    ├── agent.py        # Agent 相关的状态和决策模型
    ├── market.py       # Market 相关的状态模型
    └── world.py        # WorldState 等顶层模型
```
**关键原则:** 这个 `shared` 包**只能包含 Pydantic 模型和枚举**，绝不能包含任何业务逻辑或数据库客户端代码。

## **3. 单个服务目录结构 (示例: `state_service`)**

所有服务都遵循相似的内部结构。

```
services/state_service/
├── app/
│   ├── __init__.py
│   ├── main.py             # FastAPI 应用实例和路由定义
│   ├── crud.py             # 封装 Redis 操作的 CRUD 函数
│   └── db.py               # Redis 连接管理
├── Dockerfile              # 用于构建该服务的 Docker 镜像
└── requirements.txt        # 该服务的 Python 依赖
```

## **4. 运行流程示例 (一个Tick)**

1.  **启动:** 用户运行 `docker-compose up`，启动所有微服务和 Redis 实例。
2.  **接收决策:** `玩家策略` 向 `API网关` 发送 `POST /decisions` 请求。
3.  **转发:** `API网关` 将请求直接转发给 `编排器服务`。
4.  **编排开始:** `编排器服务` 收到请求，开始执行一个 Tick 的逻辑。
5.  **获取状态:** `编排器` 向 `状态服务` 发送 `GET /state/full` 请求。
6.  **状态服务响应:** `状态服务` 从 Redis 读取数据，组装成 `WorldState` 模型，并返回给 `编排器`。
7.  **调用逻辑:**
    *   `编排器` 将 `WorldState` 数据作为请求体，向 `市场逻辑服务` 发送 `POST /clear_markets` 请求。
    *   `市场逻辑服务` 执行计算，返回一个包含交易结果的 `MarketClearingResult` 模型。
8.  **更新状态:** `编排器` 将 `MarketClearingResult` 转换为更新指令，向 `状态服务` 发送 `PATCH /state/agents` 请求。
9.  **状态服务执行更新:** `状态服务` 解析更新指令，并执行相应的 Redis 命令（如 `HINCRBYFLOAT`）。
10. **记录日志:** `编排器` 向 `日志服务` 发送 `POST /logs` 请求，内容为需要记录的数据。
11. **循环结束:** Tick 完成。

这种架构虽然增加了网络通信的开销，但换来了极高的**灵活性**和**可维护性**。任何一个服务都可以被独立地修改、测试、部署和扩展，而不会影响到系统的其他部分。

---
**下一步:**
*   [数据模型设计](./2_DATA_MODEL.md)
