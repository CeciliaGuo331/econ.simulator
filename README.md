# econ.simulator - 宏观经济多代理人仿真平台

本项目旨在构建一个用于教学和实验的宏观经济多代理人（Multi-Agent）仿真平台。它允许使用者通过配置或编写代理人策略，来观察和参与一个微型虚拟经济的动态演化。

## 核心设计理念

### 经济学设计 (`/docs/econ_design`)

我们的经济模型基于一个离散时间的微观基础框架。世界由不同类型的**代理人**（如个人、企业、银行、政府）组成，它们在多个**市场**（商品、劳动、金融）中进行交互。

为了清晰地刻画经济活动的因果链条，我们将时间切分为“天(Day)”和“时刻(Tick)”。**每一天包含三次独立的事件循环（Ticks）**，每个循环都包含完整的计划、生产、交易和结算阶段。这种设计避免了“在收到工资的同一瞬间就用它消费”这类逻辑悖论，使得代理人的决策过程更加贴近现实。

### 技术架构设计 (`/docs/dev_handbook`)

我们追求一个**高内聚、低耦合**的系统。架构与目录划分、存储方案、API 清单等信息统一维护在 `docs/dev_handbook` 系列文档中：

- [系统架构](docs/dev_handbook/1_SYSTEM_ARCHITECTURE.md) 介绍分层职责、代码包关系与执行流程；
- [数据与持久化](docs/dev_handbook/2_DATA_AND_STORAGE.md) 详解 Redis/PostgreSQL 的协作模型；
- [API 参考](docs/dev_handbook/4_API_REFERENCE.md) 汇总对外端点与脚本工作流；
- [部署与运行指南](docs/dev_handbook/5_DEPLOYMENT.md) 指导本地开发、Docker Compose 以及生产部署。

通过这种方式，我们在单个应用内部实现了微服务级别的逻辑解耦，同时保留了单体应用的性能和便利性，且文档仅在一处维护，避免重复。

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

## 教学 / 演示环境一键播种

- `scripts/seed_test_world.py` 可一次性播种教学仿真 `test_world` 所需的 404 个主体（400 户家户 + 4 个单体主体）。
- 可通过以下命令覆盖旧脚本并重建所有账号：

	```bash
	python scripts/seed_test_world.py --simulation-id test_world --overwrite
	```

- 当传入 `--households` 参数时，会自动向上取到 `max(400, world_settings.yaml 中指定的家户数)`，确保覆盖守护阈值满足。
- 基线家户脚本的实体 ID 固定为纯数字 `900000`，与教学播种生成的 `000`~`399` 家户互不冲突；若需要复位状态，请保留 `--overwrite` 开关。

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
