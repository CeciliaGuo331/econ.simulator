# 开发者部署指南

本文档面向需要在本地或服务器环境部署 `econ.simulator` 项目的开发者，涵盖环境准备、依赖安装、配置项、运行方式以及生产部署建议。

## 1. 环境要求

- 操作系统：macOS、Linux 或其他类 Unix 系统（Windows 建议使用 WSL2）。
- Python：建议使用 **Python 3.11**（兼容 3.10+），需要具备 `venv` 或 `conda` 以隔离依赖。
- 可选组件：
  - Redis 7+（若需要用 Redis 持久化用户数据与会话）。
  - 反向代理（如 Nginx）用于生产环境的 TLS 终结与静态资源缓存。

## 2. 代码获取与虚拟环境

```bash
# 克隆仓库
git clone https://github.com/CeciliaGuo331/econ.simulator.git
cd econ.simulator

# 建议使用 conda
conda create -n econsim python=3.11
conda activate econsim
```

## 3. 安装依赖

项目所有依赖维持在 `requirements.txt` 中，执行：

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

关键依赖说明：
- `fastapi` / `uvicorn`：Web API 与 ASGI 服务。
- `jinja2`、`python-multipart`、`itsdangerous`：模板渲染与表单、会话支持。
- `redis`：如启用 Redis 存储需要。
- `pytest`、`httpx`：测试相关。

## 4. 配置项与环境变量

| 配置项 | 说明 | 默认值/位置 |
| ------ | ---- | ----------- |
| `SESSION_SECRET` | FastAPI 会话中间件的密钥，建议在生产环境通过环境变量注入。 | 默认硬编码为 `econ-sim-session-key`，可在部署脚本中覆盖。 |
| `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` | 系统会自动种子化管理员账号，用于首登。 | 见 `econ_sim/auth/user_manager.py`，默认 `admin@econ.sim` / `ChangeMe123!`。部署后请尽快修改密码。 |
| Redis 连接信息 | 若启用 Redis 存储，需要提供 `REDIS_URL` 或单独参数。 | 目前需在代码中注入，见下节。 |

### 覆盖 Session Secret（推荐）

部署启动命令中使用环境变量并传入到 `econ_sim/main.py`：

```bash
export SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uvicorn econ_sim.main:create_app --factory --host 0.0.0.0 --port 8000
```

若希望保持当前的简单 `FastAPI(title=...)` 初始化方式，可在自定义入口中读取环境变量并传递给 `SessionMiddleware`。

### 切换 Redis 存储（可选）

默认使用内存存储 `InMemoryUserStore`，适合单进程开发环境。若需多实例或持久化登录状态，可按如下步骤替换：

1. 在 `econ_sim/auth/__init__.py` 中创建 Redis 客户端并改用 `RedisUserStore`。
2. 示例：

    ```python
    import redis.asyncio as redis
    from econ_sim.auth.user_manager import RedisUserStore, UserManager

    redis_client = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    user_manager = UserManager(RedisUserStore(redis_client))
    ```

3. 确保部署环境中 Redis 以守护进程方式运行，并开放给应用访问。

## 5. 本地开发启动

1. 启动后端 API + Web 界面：

    ```bash
    uvicorn econ_sim.main:app --reload --host 0.0.0.0 --port 8000
    ```

2. 浏览器访问：
   - Swagger 文档（交互式 API 调试）：`http://localhost:8000/docs`
   - Web 登录/仪表盘：`http://localhost:8000/web/login`

3. 默认管理员账号：`admin@econ.sim` / `ChangeMe123!`

4. 运行测试确保变更未破坏既有逻辑：

    ```bash
    python -m pytest
    ```

## 6. 生产部署建议

### 6.1 使用 Uvicorn + Gunicorn（示例）

```bash
pip install "uvicorn[standard]" gunicorn

# systemd/service 级别命令示例
exec gunicorn econ_sim.main:app \
  --bind 0.0.0.0:8000 \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --timeout 120
```

- 根据 CPU 核心数调整 `--workers`。
- 在反向代理（如 Nginx）层面启用 TLS、压缩与静态资源缓存。
- 将静态文件 `/web/static` 映射到代理服务器以减少应用负载。

### 6.2 进程与日志管理

- 使用 `systemd`、`supervisord` 或容器 orchestration（Docker/Kubernetes）控制进程生命周期。
- 通过 `uvicorn --log-config` 自定义日志级别，生产环境建议至少保留访问日志与错误日志。

### 6.3 资源和安全

- 使用强随机 Session Secret，并考虑开启 `SessionMiddleware` 中的 `https_only=True`（需 HTTPS）。
- 首次部署后立即登录管理员账号修改密码。
- 若启用 Redis，确保访问受密码或内网限制。

## 7. 静态资源与文档

- 模板位于 `econ_sim/web/templates/`，样式表在 `econ_sim/web/static/`。
- 部署后访问 `/web/docs` 获取内置操作说明。
- 需要扩展前端时，可在构建脚本中增加静态资源压缩、缓存策略。

## 8. 验证部署

部署完成后建议进行以下快速检查：

1. 健康检查：访问 `GET /health`，返回 `{"status": "ok"}` 即正常。
2. 登录流程：使用管理员账号登录 `/web/login`，确认仪表盘显示。
3. API 调用：通过 Swagger 或 `curl` 调用 `/auth/login`、`/simulation/{id}/state` 验证功能。
4. 脚本上传：使用普通用户账号测试 `/web/scripts` 上传能力。

若遇到问题，请参考 `/docs/code_structure/`、`/docs/econ_design/` 了解系统结构，或查看日志定位错误。
