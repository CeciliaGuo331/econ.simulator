# 开发者手册概览

欢迎来到 `econ.simulator`。这是一套“平台 + 仿真世界”双层结构的教学用宏观经济沙盒：上层平台负责账户、脚本、可视化、运维，下层世界负责经济模型和 Tick 计算。本手册帮助你在接手后的前几个小时内建立完整的心智模型。

## 如何阅读本手册

| 文档 | 你能在其中找到什么 |
| ---- | -------------------- |
| [1_SYSTEM_ARCHITECTURE.md](./1_SYSTEM_ARCHITECTURE.md) | 平台 vs 仿真世界的边界、关键模块、调用链、依赖关系图 |
| [2_DATA_AND_STORAGE.md](./2_DATA_AND_STORAGE.md) | Redis/PostgreSQL 结构、脚本仓库设计、数据流、接口契约 |
| [3_PROGRESS_AND_TODO.md](./3_PROGRESS_AND_TODO.md) | 近期交付、质量状态、正在推进的里程碑与下一阶段目标 |
| [4_API_REFERENCE.md](./4_API_REFERENCE.md) | REST API 对照表、脚本工作流、典型请求/响应示例 |
| [5_DEPLOYMENT.md](./5_DEPLOYMENT.md) | 本地/容器化部署、运维监控、告警策略、账号播种脚本 |
| [../user_strategies/](../user_strategies/) | 面向各主体的策略指南、沙箱 API、代码模板 |

推荐流程：先阅读架构与数据章节，快速理解分层；然后结合 README 与代码动手实践；最后根据场景查阅 API、部署与策略指南。

## 项目速览

- **技术栈**：FastAPI + Uvicorn（服务 & Web）、Redis（世界状态）、PostgreSQL（脚本与配置）、Jinja2（后台界面）。
- **核心入口**：
  - `econ_sim/main.py` —— 创建 FastAPI 服务、挂载路由、播种教学仿真。
  - `econ_sim/core/orchestrator.py` —— 仿真调度器，封装 Tick 生命周期、脚本执行、状态写回。
  - `econ_sim/script_engine/` —— 脚本上传、存储、沙箱执行、覆盖策略。
- **常用命令**：
  - `bash scripts/dev_start.sh`：启动开发环境（含依赖服务）。
  - `python scripts/seed_test_world.py --overwrite`：播种教学仿真 `test_world`。
  - `pytest`：运行单元 / 集成测试。
  - `docker compose up -d`：容器化启动 FastAPI + Redis + PostgreSQL。

## 与团队协作

- 分层：平台逻辑（账户、脚本、API、监控）与仿真模型（Tick 逻辑、市场模块）保持解耦；新增业务要么走 API / Script Engine，要么在 `logic_modules/` 内实现。
- 质量：每一次 PR 应附最小化测试（或更新现有测试），确保 `pytest` 完整通过。
- 文档：手册即代码说明书，更新功能时请同步文档；若新增外部接口，请补充 [4_API_REFERENCE.md](./4_API_REFERENCE.md)。

准备好以后，继续阅读 [系统架构](./1_SYSTEM_ARCHITECTURE.md)。
