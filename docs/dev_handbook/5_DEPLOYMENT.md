# 部署与运行指南

本章覆盖开发、测试、生产环境的搭建方式，强调“平台层服务”与“仿真执行层”在部署上的协同，并为未来的每日 Tick 调度与脚本轮换预留配置位。

## 1. 环境矩阵

| 角色 | 组件 | 版本建议 | 说明 |
| ---- | ---- | -------- | ---- |
| 平台层 | Python | 3.11（兼容 3.10+） | FastAPI、脚本引擎、Web 视图运行时 |
| 仿真执行 | Redis | 7.x | 存储世界状态、Tick 日志；可单机部署，也可容器化 |
| 数据持久化 | PostgreSQL | 14+ | 脚本仓库 + 计划引入的策略版本、交易归档 |
| 自动化 | Docker / Docker Compose | 最新稳定版 | 统一拉起 app + Redis + Postgres |

```bash
# 克隆仓库并准备虚拟环境（示例使用 conda）
git clone https://github.com/CeciliaGuo331/econ.simulator.git
cd econ.simulator
conda create -n econsim python=3.11 -y
conda activate econsim
pip install -r requirements.txt
```

## 2. 配置清单

| 环境变量 | 作用 | 平台/仿真层影响 | 备注 |
| -------- | ---- | ---------------- | ---- |
| `ECON_SIM_SESSION_SECRET` | SessionMiddleware 密钥 | 平台 | 生产必须使用强随机值，可用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成 |
| `ECON_SIM_POSTGRES_DSN` | PostgreSQL 连接串 | 平台 | 启用后脚本库落地 Postgres；缺省时退化为内存模式（仅限测试） |
| `ECON_SIM_REDIS_URL` | Redis 连接串 | 仿真 | 缺省时使用内存状态存储，无法持久化 Tick 数据 |
| `ECON_SIM_POSTGRES_MIN_POOL` / `MAX_POOL` | `asyncpg` 连接池 | 平台 | 根据 API 并发调整 |
| `ECON_SIM_POSTGRES_SCHEMA` / `SCRIPT_TABLE` | 表定位 | 平台 | 未来新增 `script_versions`、`agent_snapshots` 时复用 |
| `ECON_SIM_DAY_PLAN_ENABLED` *(预留)* | 日终任务开关 | 平台+仿真 | 为每日 Tick 批处理准备的布尔开关 |

建议：在 `config/dev.env` 维护本地配置，`scripts/dev_start.sh` 会自动加载；生产部署使用 `.env` 或密钥管理服务。

## 3. 本地开发入口

```bash
# 启动带热重载的 API 服务
uvicorn econ_sim.main:app --reload --host 0.0.0.0 --port 8000

# 访问交互式文档与 Web 界面
# API Swagger: http://localhost:8000/docs
# Web 登录页: http://localhost:8000/web/login

# 运行单元 & 集成测试
pytest

# （可选）一键拉起依赖并启动应用
./scripts/dev_start.sh
```

- 默认管理员：`admin@econ.sim` / `ChangeMe123!`（首次登录后请重置）。
- `dev_start.sh` 会按需拉起 Docker 内的 PostgreSQL 与 Redis，可通过 `START_POSTGRES=0`、`START_REDIS=0` 跳过。
- 访问入口：Swagger `http://localhost:8000/docs`，Web 登录页 `http://localhost:8000/web/login`。

## 4. Docker Compose 一键启动

仓库根目录已提供 `docker-compose.yml`：

```bash
docker compose up -d
```

- 服务暴露端口：应用 `8000`、PostgreSQL `5432`、Redis `6379`。
- 默认使用 `config/docker.env` 提供的环境变量，可按需复制修改。
- 持久化目录：PostgreSQL → `postgres-data` 卷，Redis → `redis-data` 卷。
- 用户上传的策略脚本会持久化在 PostgreSQL `scripts` 表中，对应数据卷 `postgres-data`。
- 首次启动应用时，会自动播种一个管理员账号（`admin@econ.sim`）与五个基线账户，默认口令如下：
  | 邮箱 | 角色 | 默认密码 |
  | ---- | ---- | -------- |
  | `admin@econ.sim` | 管理员 | `ChangeMe123!` |
  | `baseline.household@econ.sim` | 家户代理 | `BaselinePass123!` |
  | `baseline.firm@econ.sim` | 企业代理 | `BaselinePass123!` |
  | `baseline.bank@econ.sim` | 商业银行 | `BaselinePass123!` |
  | `baseline.central_bank@econ.sim` | 央行代理 | `BaselinePass123!` |
  | `baseline.government@econ.sim` | 政府代理 | `BaselinePass123!` |
  启动后请尽快登录管理员账号并修改密码，再按需更新基线账户密码或禁用。
- 需要模拟真实用户时，可运行 `python scripts/seed_baseline_scripts.py --simulation <sim-id> --attach --overwrite` 将
  `deploy/baseline_scripts/` 下的五类基线脚本写入数据库；在 Docker Compose 中执行：

  ```bash
  docker compose run --rm app python scripts/seed_baseline_scripts.py --simulation demo-sim --attach --overwrite
  ```

  该脚本会为五类角色创建脚本记录（用户 ID 形如 `baseline.<role>@econ.sim`），可重复运行保持幂等。

## 5. 健康检查与运维

| 指标 | 端点/方式 | 备注 |
| ---- | --------- | ---- |
| 健康检查 | `GET /health` | 返回 `{ "status": "ok" }` |
| Swagger | `GET /docs` | FastAPI 自动生成的接口说明 |
| 静态资源 | `/web/static` | 可由反向代理托管 |
| Tick/脚本日志 | Redis `sim:{id}:logs` | 用于可视化或调试 |
| 世界状态快照 | Redis `sim:{id}:state` | 计划通过后台任务定期持久化 |

监控建议：结合 Prometheus/OpenTelemetry 跟踪 Redis 命中率、PostgreSQL 写延迟、脚本执行错误计数，为后续日终批处理提供基线。

### 5.1 观测指标实例

- **主体覆盖率**：在 `SimulationOrchestrator._require_agent_coverage` 成功后记录 `coverage.<kind>` 指标。
- **脚本失败率**：对 `ScriptRegistry` 捕获的 `ScriptExecutionError` 计数，并按仿真实例聚合。
- **日终轮换耗时** *(规划)*：记录 `ScriptRegistry.rotate_scripts` 执行时间，监控日终批处理窗口。

### 5.2 告警策略与抑制

- 覆盖率低或缺少脚本 → 立即告警；可在播种窗口配置抑制规则。
- 失败率提升 → Warning（单 Tick 少量失败）、Critical（连续 3 Tick 以上或 5 分钟内同脚本 10 次失败）。
- Redis 持久化异常 → Critical，避免世界状态丢失。

## 6. 常见问题排查

| 现象 | 排查方向 |
| ---- | -------- |
| 登录成功但 API 返回 401 | 确认前端正确设置 `Authorization: Bearer <token>` 头，并检查 token 是否过期 |
| 上传脚本失败且返回 400 | 检查脚本是否定义 `generate_decisions(context)`，或是否引用了禁用的模块 |
| Redis 数据重启丢失 | 本地使用内存模式，改用 Docker Redis 或开启持久化配置 |
| 反向代理返回 502 | 确认 Gunicorn 进程存活、端口监听以及 `proxy_read_timeout` 配置 |

## 7. 与未来目标的衔接

| 目标 | 部署准备 | 影响面 |
| ---- | -------- | ------ |
| 每日运行 n 个 Tick | 引入后台任务执行器（Celery/Arq/自建 scheduler）；新增 `ECON_SIM_DAY_PLAN_ENABLED`、`TICKS_PER_DAY` | API 增加计划任务端点，Orchestrator 需要长时间运行保障 |
| 日终脚本轮换（继承状态） | ScriptRegistry 需访问 Redis/PG 双写；部署时确保 PostgreSQL 具备 `script_versions`、`agent_snapshots` 表 | Web/Admin UI 增加日终窗口提示；任务失败需自动回滚 |
| 交易/状态模型演进 | PostgreSQL 引入新表，Redis 结构调整；部署时运行迁移脚本 | API 提供新查询端点，监控需要新增指标 |

发布上述功能前，请在 staging 环境验证：

1. 数据迁移脚本幂等执行。
2. 日终任务对 Redis/PG 的写延迟与回滚策略。
3. 兼容现有 API 与 Web UI 的回退路径。

## 8. 后续演进

- 若需扩展至 Kubernetes，可将 FastAPI 作为 Deployment，将 Redis/PostgreSQL 以托管服务承载，结合 Service Mesh 提供可观测性。
- 结合 `docs/dev_handbook/2_DATA_AND_STORAGE.md` 的路线图，逐步将世界状态写穿至 PostgreSQL，以支持快照查询。
- 计划引入 IaC（Terraform/Ansible）模板，实现环境一键化部署；上线后请同步更新本章的命令与变量约定。
