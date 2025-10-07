# 部署与运行指南

本章汇总本地开发、测试和生产部署所需的操作步骤，统一替代历史的 `docs/deployment.md` 文档。

## 1. 环境准备

| 组件 | 版本建议 | 说明 |
| ---- | -------- | ---- |
| 操作系统 | macOS / Linux / WSL2 | Windows 用户推荐 WSL2 运行 docker 与 Python |
| Python | 3.11（兼容 3.10+） | 使用 `venv` 或 Conda 隔离依赖 |
| Redis | 7.x（可选） | 开启脚本仓库以外的持久化、协作功能 |
| PostgreSQL | 14+（可选） | 用于脚本持久化；Docker Compose 已包含 |

```bash
# 克隆仓库并创建虚拟环境（示例使用 conda）
git clone https://github.com/CeciliaGuo331/econ.simulator.git
cd econ.simulator
conda create -n econsim python=3.11 -y
conda activate econsim
pip install -r requirements.txt
```

## 2. 核心配置项

| 环境变量 | 作用 | 备注 |
| -------- | ---- | ---- |
| `ECON_SIM_SESSION_SECRET` | FastAPI `SessionMiddleware` 的密钥 | 生产环境必须设为随机值，可用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成 |
| `ECON_SIM_POSTGRES_DSN` | PostgreSQL 连接串 | 形如 `postgresql+asyncpg://user:pass@localhost:5432/econsim` |
| `ECON_SIM_POSTGRES_SCHEMA` | 脚本仓库 Schema 名称 | 默认 `public`，可按环境覆盖 |
| `ECON_SIM_POSTGRES_SCRIPT_TABLE` | 脚本表名 | 默认 `scripts` |
| `ECON_SIM_POSTGRES_MIN_POOL` / `ECON_SIM_POSTGRES_MAX_POOL` | `asyncpg` 连接池大小 | 依据并发量调节 |
| `ECON_SIM_REDIS_URL` | Redis 连接串 | 例如 `redis://localhost:6379/0`，缺省时退化为内存模式 |

建议在 `config/dev.env` 中维护开发用环境变量，`scripts/dev_start.sh` 会自动加载。

## 3. 本地开发流程

```bash
# 1. 启动 FastAPI（热重载）
uvicorn econ_sim.main:app --reload --host 0.0.0.0 --port 8000

# 2. 访问交互式文档与 Web 界面
# API Swagger: http://localhost:8000/docs
# Web 登录页: http://localhost:8000/web/login

# 3. 运行测试
pytest
```

- 默认管理员账号：`admin@econ.sim` / `ChangeMe123!`，首次登录后请立即修改密码。
- `scripts/dev_start.sh` 会检测 Docker 并拉起 Postgres + Redis，随后以 `uvicorn --reload` 启动后端，可通过环境变量 `START_POSTGRES=0` 或 `DOCKER_SERVICES="postgres redis"` 控制依赖服务。

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

## 5. 生产部署建议

### 5.1 Uvicorn + Gunicorn

```bash
pip install "uvicorn[standard]" gunicorn

gunicorn econ_sim.main:app \
  --bind 0.0.0.0:8000 \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --timeout 120
```

- 根据 CPU 核心数和预估负载调整 `--workers`。
- 建议搭配 Nginx 处理 TLS、静态资源缓存（映射 `/web/static`）。

### 5.2 安全加固

- 使用强随机的 `ECON_SIM_SESSION_SECRET`，并在反向代理启用 HTTPS 后打开 `SessionMiddleware` 的 `https_only`/`max_age` 设置。
- 首次部署后登录管理员账号重置密码。
- 对 Redis/PostgreSQL 配置网络访问控制与凭证。
- 日志：使用 `uvicorn --log-config` 或结构化日志方案，将访问日志上传至集中化平台。

## 6. 健康检查与运维

| 指标 | 端点/方式 | 备注 |
| ---- | --------- | ---- |
| 健康检查 | `GET /health` | 返回 `{ "status": "ok" }` |
| Swagger | `GET /docs` | FastAPI 自动生成的接口说明 |
| 静态资源 | `/web/static` | 可由反向代理托管 |
| Tick/脚本日志 | Redis `sim:{id}:logs` | 用于可视化或调试 |

监控建议：跟踪 Redis 命中率、PostgreSQL 写入延迟、脚本执行错误计数，可通过 Prometheus/OpenTelemetry 接入。

### 6.1 观测指标实例

- **主体覆盖率（Coverage Ratio）**：统计已播种脚本数量与期待主体数的比值，可在 `SimulationOrchestrator._require_agent_coverage` 判定通过后写入自定义指标；教学场景可聚焦指标 `coverage.household`（默认需达到 100%）。
- **脚本失败率**：基于 `record_script_failures` 与 `ScriptFailureEvent` 计数，建议按仿真实例维度统计近 N Tick 的失败率；若 failure > 0 时还应关联 fallback 触发次数。
- **Baseline Fallback 触发次数**：`BaselineFallbackManager` 在脚本报错时会接管决策，可将触发事件暴露为计数型指标，用于定位策略质量下降。

### 6.2 告警策略与抑制

- 当主体覆盖率低于 100% 或出现缺少脚本异常时，应立即通知管理员；为避免播种过程中频繁告警，可在播种脚本执行窗口（如部署阶段）暂时抑制。
- 基准策略兜底的告警建议采用分级策略：
  - **Warning**：单 Tick 出现少量脚本失败但 fallback 能成功接管；
  - **Critical**：连续超过 3 个 Tick 触发 fallback，或同一脚本在 5 分钟内失败超过 10 次。
- 为降低噪声，可在告警系统中增加抖动时间（例如 60 秒）或结合脚本版本号、用户信息做聚合，避免相同根因重复报警。

## 7. 常见问题排查

| 现象 | 排查方向 |
| ---- | -------- |
| 登录成功但 API 返回 401 | 确认前端正确设置 `Authorization: Bearer <token>` 头，并检查 token 是否过期 |
| 上传脚本失败且返回 400 | 检查脚本是否定义 `generate_decisions(context)`，或是否引用了禁用的模块 |
| Redis 数据重启丢失 | 本地使用内存模式，改用 Docker Redis 或开启持久化配置 |
| 反向代理返回 502 | 确认 Gunicorn 进程存活、端口监听以及 `proxy_read_timeout` 配置 |

## 8. 后续演进

- 若计划拆分前后端，可将当前 FastAPI 项目以 ASGI 方式部署在 Kubernetes，并通过 Service Mesh 提供观测。
- 结合 `docs/dev_handbook/2_DATA_AND_STORAGE.md` 中的路线图，引入 PostgreSQL 作为世界状态的权威持久层。
- 封装部署脚本（Ansible 或 Terraform）以实现一键化上线与环境一致性。
