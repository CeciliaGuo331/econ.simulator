# **代码结构设计 (模块化单体)**

本设计遵循“高内聚、低耦合”的原则，在一个单一的应用内通过清晰的模块划分来实现。

## **1. 根目录结构**

```
econ.simulator/
├── .venv/                  # Python 虚拟环境
├── config/                 # 仿真配置文件 (如 world_settings.yaml)
├── data/                   # 输出的 Parquet 日志数据
├── docs/                   # 项目文档
├── econ_sim/               # Python 核心代码包
├── scripts/                # 辅助脚本 (如启动仿真、运行分析)
├── tests/                  # 测试代码
├── .gitignore
├── pyproject.toml          # 项目元数据和依赖管理
└── README.md
```

## **2. `econ_sim/` 核心代码包结构**

这是项目的主体，所有核心逻辑都在这里。

```
econ_sim/
├── __init__.py
├── api/                    # 对外 API 层
│   ├── __init__.py
│   └── endpoints.py        # FastAPI 路由定义
│
├── core/                   # 核心编排与调度
│   ├── __init__.py
│   └── orchestrator.py     # 包含主事件循环的编排器
│
├── data_access/            # 数据访问层
│   ├── __init__.py
│   ├── models.py           # 核心 Pydantic 数据模型
│   └── redis_client.py     # 封装与 Redis 交互的异步客户端
│
├── logic_modules/          # 【关键】所有业务逻辑模块
│   ├── __init__.py
│   ├── agent_logic.py      # 代理人行为逻辑
│   ├── market_logic.py     # 市场结算逻辑
│   └── ...                 # 其他独立的逻辑模块
│
├── utils/                  # 通用工具模块
│   ├── __init__.py
│   └── settings.py         # 配置加载模块
│
└── main.py                 # FastAPI 应用的启动入口
```

## **3. 模块职责与依赖规则**

*   **`main.py`**: 程序的入口。创建并配置 FastAPI 应用实例。

*   **`api`**: 对外接口层。
    *   **职责**: 定义 FastAPI 的路由，处理 HTTP 请求和响应。
    *   **依赖**: 只能调用 `core` 模块。

*   **`core`**: 编排层。
    *   **职责**: 实现仿真的主事件循环。它不包含具体业务逻辑，而是通过调用 `logic_modules` 和 `data_access` 来“编排”一个 Tick 的流程。
    *   **依赖**: 可以调用 `logic_modules` 和 `data_access`。

*   **`logic_modules`**: 业务逻辑层。
    *   **职责**: 实现所有具体的经济学计算逻辑。每个文件（如 `market_logic.py`）都是一个独立的、无状态的纯函数集合。
    *   **依赖**: **绝对不能**依赖 `core` 或 `api`。它们是纯粹的计算单元，需要的数据通过函数参数传入。

*   **`data_access`**: 数据访问层。
    *   **职责**: **唯一**负责与数据库（Redis）交互的模块。它封装了所有的数据读写操作，并定义了 Pydantic 数据模型。
    *   **依赖**: 不依赖任何其他内部模块。

**依赖关系图:**

```
[API] -> [Core] -> [Logic Modules]
   |         |
   +------> [Data Access] <------+
```
这个严格的单向依赖链确保了系统的可维护性和低耦合性。

## **4. 运行流程示例 (一个Tick)**

1.  **启动:** 用户运行 `uvicorn econ_sim.main:app` 启动 FastAPI 应用。
2.  **接收决策:** `玩家策略` 向 `api.endpoints` 发送 `POST /decisions` 请求。
3.  **转发:** API 层调用 `core.orchestrator` 中的函数来处理该请求。
4.  **编排开始:** `Orchestrator` 开始执行一个 Tick 的逻辑。
5.  **获取状态:** `Orchestrator` 调用 `data_access.redis_client` 中的函数，从 Redis 读取完整的世界状态。
6.  **调用逻辑:**
    *   `Orchestrator` 将状态数据作为参数，调用 `logic_modules.market_logic.clear_markets()`。
    *   `market_logic` 执行计算，返回一个包含交易结果的数据结构。
7.  **更新状态:** `Orchestrator` 将交易结果传递给 `data_access.redis_client` 中的函数，以更新 Redis 中的状态。
8.  **循环结束:** Tick 完成。所有交互都在内存中的函数调用完成，高效且易于调试。

---
**下一步:**
*   [数据模型设计](./2_DATA_MODEL.md)
