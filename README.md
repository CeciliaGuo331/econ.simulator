# econ.simulator - 宏观经济多代理人仿真平台

本项目旨在构建一个用于教学和实验的宏观经济多代理人（Multi-Agent）仿真平台。它允许使用者通过配置或编写代理人策略，来观察和参与一个微型虚拟经济的动态演化。

## 核心设计理念

### 经济学设计 (`/docs/econ_design`)

我们的经济模型基于一个离散时间的微观基础框架。世界由不同类型的**代理人**（如个人、企业、银行、政府）组成，它们在多个**市场**（商品、劳动、金融）中进行交互。

为了清晰地刻画经济活动的因果链条，我们将时间切分为“天(Day)”和“时刻(Tick)”。**每一天包含三次独立的事件循环（Ticks）**，每个循环都包含完整的计划、生产、交易和结算阶段。这种设计避免了“在收到工资的同一瞬间就用它消费”这类逻辑悖论，使得代理人的决策过程更加贴近现实。

### 技术架构设计 (`/docs/code_structure`)

我们追求一个**高内聚、低耦合**的系统。为了在保证开发效率和运行性能的同时实现这一目标，我们最终选择了**模块化单体（Modular Monolith）**架构。

系统是一个单一的、统一的应用程序，但其内部被严格划分为具有清晰边界和职责的模块（如 `api`, `core`, `logic_modules`, `data_access`）。

这种设计的核心优势在于：
1.  **关注点分离**: 经济逻辑的实现（在 `logic_modules` 中）与系统状态的管理（在 `data_access` 层）被严格分开。逻辑模块是无状态的纯函数，只负责计算。
2.  **清晰的依赖关系**: 模块间的依赖关系是单向的 (`API -> Core -> Logic/DataAccess`)。这防止了循环依赖，使得代码库易于理解和维护。
3.  **高性能与低复杂性**: 所有模块间的“通信”都是在内存中进行的直接函数调用，避免了微服务架构中网络通信和数据序列化的开销。这使得系统更简单、更易于调试和部署。

通过这种方式，我们在单个应用内部实现了微服务级别的逻辑解耦，同时保留了单体应用的性能和便利性。

## 项目状态

**当前阶段：** 核心框架与基础策略已完成，包含完整的 API、编排引擎、数据访问层以及商品/劳动/金融市场的出清逻辑。内置的基准策略可用于快速烟雾测试。

详细的经济和技术设计文档位于 `/docs` 目录下，代码实现对应目录 `econ_sim/`。

## 快速开始

1. 激活虚拟环境并安装依赖：

	```bash
	conda activate econsim
	pip install -r requirements.txt
	```

2. 运行内置的演示脚本（执行 3 个 Tick）：

		```bash
		python scripts/run_simulation.py
		```

3. 启动 FastAPI 服务：

	```bash
	uvicorn econ_sim.main:app --reload
	```

   或者使用一键脚本（会自动尝试启动本地 PostgreSQL 并加载 `config/dev.env`）：

	```bash
	bash scripts/dev_start.sh
	```

   > 首次使用前运行一次 `chmod +x scripts/dev_start.sh` 赋予可执行权限。

4. 运行测试套件：

		```bash
		pytest
		```

## 本地开发环境说明

- **Docker Compose 全栈启动**：`docker-compose.yml` 已包含 FastAPI 应用、PostgreSQL、Redis 三个服务。

	```bash
	docker compose up -d
	```

	- 应用服务默认暴露在 `http://localhost:8000`。
	- 数据持久化目录：PostgreSQL → `docker volume postgres-data`，Redis → `docker volume redis-data`。
	- 应用环境变量定义在 `config/docker.env`，包含服务内部网络地址（`postgres`、`redis`）。

- **单独启动数据库/缓存（可选）**：

	```bash
	docker compose up -d postgres redis
	```

- **环境变量**：
	- `ECON_SIM_POSTGRES_DSN`：PostgreSQL 连接串。
	- `ECON_SIM_REDIS_URL`：Redis 连接串。
	- `ECON_SIM_SESSION_SECRET`：Session 中间件密钥（`econ_sim/main.py` 会读取此变量）。
	- 将本地开发所需的变量写入 `config/dev.env`，启动脚本会自动加载。

- **一键启动脚本（热重载）**：`scripts/dev_start.sh` 会在检测到 Docker 时拉起 Postgres + Redis，再读取 `config/dev.env` 后以 `uvicorn --reload` 方式启动应用；通过 `START_POSTGRES=0 bash scripts/dev_start.sh` 可跳过 Docker 服务启动，或使用 `DOCKER_SERVICES="postgres redis"` 指定具体服务。

## API 速览

| 方法 | 路径 | 描述 |
| ---- | ---- | ---- |
| `POST` | `/simulations` | 创建新的仿真实例 |
| `GET` | `/simulations/{id}` | 查询仿真状态 |
| `POST` | `/simulations/{id}/run_tick` | 执行下一 Tick，可附带决策覆盖 |
| `GET` | `/simulations/{id}/state/full` | 返回完整世界状态快照 |
| `GET` | `/simulations/{id}/state/agents` | 查询指定代理人（默认全部家户） |
| `POST` | `/scripts` | 上传脚本到个人脚本库 |
| `GET` | `/scripts` | 列出当前用户的脚本（含未挂载） |
| `POST` | `/simulations/{id}/scripts/attach` | 将个人脚本挂载到仿真实例 |

更多细节请参考 `econ_sim/api/endpoints.py`。
