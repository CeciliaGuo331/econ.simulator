# 开发者手册概览

> 读者定位：本手册面向刚接手 `econ.simulator` 项目的开发者，帮助你在最短时间内掌握系统架构、数据持久化方案、现有进度与后续工作。

## 文档结构

| 文档 | 内容摘要 |
| ---- | -------- |
| [1_SYSTEM_ARCHITECTURE.md](./1_SYSTEM_ARCHITECTURE.md) | 代码模块划分、执行流程、依赖边界与关键技术栈 |
| [2_DATA_AND_STORAGE.md](./2_DATA_AND_STORAGE.md) | Redis + PostgreSQL 的混合存储方案、脚本库设计、Mermaid 数据关系图 |
| [3_PROGRESS_AND_TODO.md](./3_PROGRESS_AND_TODO.md) | 当前功能进展、质量状态、短期与中期 TODO 列表 |

你可以按顺序阅读，也可以按需跳转。建议阅读完架构和数据两篇后，再回到 README 和代码获取动手实践的上下文。

## 快速参考

- **运行时栈**：FastAPI + Uvicorn、Redis（仿真状态）、PostgreSQL（脚本持久化）、前端模板基于 Jinja2。
- **主要命令**：
  - 启动开发环境：`bash scripts/dev_start.sh`
  - 单元测试：`pytest`
  - Docker 编排：`docker compose up -d`
- **核心入口**：`econ_sim/main.py`（FastAPI 应用）、`econ_sim/core/orchestrator.py`（仿真调度器）。

## 协作建议

1. **保持分层边界**：新增逻辑请尽量落在 `logic_modules/`，避免跨层依赖。
2. **优先编写测试**：`tests/` 目录已经覆盖脚本引擎、仿真流程与认证逻辑，新增功能请补充最小化测试。
3. **文档共建**：手册随代码演进同步更新，提 PR 时可附带相关文档调整。

准备好了的话，让我们从 [系统架构](./1_SYSTEM_ARCHITECTURE.md) 开始。
