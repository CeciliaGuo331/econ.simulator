# 进度与待办

本章汇总当前成果、质量基线与路线图，让新成员快速了解“平台层 ↔ 仿真世界”双层架构的推进节奏。

## 1. 版本快照（截至 2025-10-17）

### 1.1 平台层交付

| 模块 | 状态 | 与仿真世界的接口 |
| ---- | ---- | ---------------- |
| API (`econ_sim/api/`) | ✅ 稳定 | 通过 `SimulationOrchestrator`、`ScriptRegistry` 暴露仿真与脚本生命周期 |
| Web (`econ_sim/web/`) | ✅ 稳定 | 使用 API + Redis 会话展示状态、触发脚本挂载 |
| 认证 (`econ_sim/auth/`) | ✅ 稳定 | `UserManager` 维护令牌，供 API 层鉴权 |
| 脚本引擎 (`econ_sim/script_engine/`) | ✅ 稳定 | 注册、沙箱执行脚本，向 orchestrator 返回 `StateUpdateCommand` |
| 脚本持久化 (`postgres_store.py`) | ✅ 稳定 | PostgreSQL 表自愈、支持脚本限额与版本号 |

### 1.2 仿真世界交付

| 模块 | 状态 | 功能摘要 |
| ---- | ---- | -------- |
| `SimulationOrchestrator` | ✅ 稳定 | 创建/销毁仿真、推进 Tick/Day、协调脚本决策 |
| `logic_modules/agent_logic.py` | ✅ 稳定 | 家户、企业、银行、央行、政府的决策钩子 |
| `logic_modules/market_logic.py` | ✅ 稳定 | 暂支持单市场清算，预留扩展点 |
| `logic_modules/shock_logic.py` | ✅ 稳定 | 家户冲击模型，可通过 API 控制 |
| `DataAccessLayer` | ✅ 稳定 | Redis 世界状态、Tick 日志、参与者列表的唯一写入口 |

### 1.3 数据与基础设施

| 项 | 状态 | 说明 |
| -- | ---- | ---- |
| 测试覆盖 | ✅ `pytest` 全通过 | 涵盖脚本生命周期、仿真流程、认证、数据访问 |
| 开发脚本 | ✅ `scripts/dev_start.sh` | 自动拉起 Postgres + Redis + 应用 |
| 文档体系 | ✅ `docs/dev_handbook` | 章节按照层次拆分，保持单一事实来源 |
| Docker | ✅ `docker-compose.yml` | 应用 + Postgres + Redis 一体化启动 |

## 2. 近期成果

- **脚本工作流**：个人脚本库 → 仿真挂载 → Tick 决策合并一体化；支持脚本限额与基线兜底。
- **数据契约**：Redis 存储世界状态，PostgreSQL 持久化脚本及限额；接口使用 Pydantic 模型保证类型安全。
- **启动播种**：应用启动自动播种管理员与基线主体，`seed_baseline_scripts.py` 实现幂等更新。
- **文档重构**：架构、数据章节更新，帮助区分平台层与仿真层职责。

### 2.1 每日 Tick 批处理 & 日终脚本换代（新增）

- 管理端“执行 1 day”能力：支持输入当日 Tick 数（空则使用配置），对应后端 `SimulationOrchestrator.run_day`。
- 日终脚本轮换（基础版）：在日终边界替换脚本代码并保留实体，下一交易日生效；后端增加 `update_script_code_at_day_end`，注册表支持 `update_script_code`；用户界面提供轮换表单。
- 测试补充：`run_day` 自定义 Tick、日终轮换成功与非日终失败用例。

### 2.2 历史数据持久化与对外查询（新增）

- 每 Tick 日志持久化（PostgreSQL）：新增 `tick_logs` 表，记录 `simulation_id/tick/day/message/context/recorded_at`，用于后续分析与历史查询。
- 历史查询 API：`GET /simulations/{simulation_id}/history/tick_logs` 支持按 tick/day 区间与 message 过滤，支持分页（limit/offset）。
- 数据访问层：在每次 `record_tick` 时将日志写入 Postgres（如果配置了 `ECON_SIM_POSTGRES_DSN`），并保留内存/Redis 最近日志用于页面显示。

### 2.3 平台与前端增强（新增）

- 仪表盘功能拆分：
	- 用户与管理员仪表盘拆分为多个标签页
- 并发性能优化（Web 层）：
	- 引入有上限的 `_bounded_gather`，在多仿真/多用户聚合时并行获取 state/features/failures，显著降低页面等待时间。
- LLM API 封装与调用限制：
	- 新增 `/llm/completions` 路由，提供安全的模型接口（默认 Mock Provider），并对每用户进行速率限制；错误处理与配额返回信息完善。
- 交易与运行时数据（基础设施）：
	- 新增模型：`MarketRuntime`、`TradeRecord`、`LedgerEntry`、`AgentSnapshotRecord`。
	- Redis 增加 runtime 存储（`market_runtime`、`trades`、`ledger`），DataAccessLayer 提供写入/读取接口。
	- Postgres 增加 `scripts_versions`（追加式脚本版本）与 `agent_snapshots`（草案）表；数据模型文档同步更新。
- 用户个人主页（MVP）：
	- 新增 `/web/profile` 页面，支持头像（文件/URL）、昵称、密码、邮箱更新；顶部导航显示头像。
	- 显示该用户相关的近期脚本失败事件（通知 MVP）；整体表单与按钮样式精简与现代化。
- 管理与体验修复：
	- Admin “执行 1 day” 默认 ticks 与“补齐当日”按钮；批量脚本重新挂载工具；Docker 播种路径修正；最近日志下载链接。

## 3. TODO

### 数据库
- [已完成] 每 tick 交易/日志数据持久化（PostgreSQL `tick_logs`）。
- [已完成] 历史数据对外查询 API（`/simulations/{id}/history/tick_logs`）。
- [进行中] 交易与账本归档：设计 `trades_ledger` 类表结构（或分表）与分页查询 API；与 Redis runtime 写穿/双写策略对齐。

### 世界设计
- [已完成] 每日 Tick 批处理：管理员界面改为“执行 1 day”，支持输入当日 Tick 数。
- [已完成] 日终脚本轮换：日终边界替换脚本代码，实体状态保留，下一交易日生效。
- **考虑加入每日定时自动执行n tick的功能**

### 经济系统设计

- **经济模型重构**：当前市场和各种主体的设计未遵循文档中的经济设计，仅作平台测试用，需要重构。

### 用户
- [已完成] LLM API 封装与配额限制：提供 `/llm/completions`，默认 Mock Provider，支持每用户速率限制与用量回报。
- **经济模型重构后平台策略脚本相关api需要相应修改，以及更新网站文档页**

### 网页
- **数据渲染加强**
- [已完成] 网页性能优化（并发）：多仿真聚合异步化、并发上限控制。
- [部分完成] 用户个人主页：基础资料与头像已实现；通知为 MVP（显示近期失败事件），后续补充通知存储/标记已读/分页。
- [已完成] 仪表盘功能拆分：用户/管理员 Tab 化与默认兼容
- **网页上的用户文档补充**：用户编写脚本时，阅读网站上的文档应当能获得足够多信息。
- **文档页渲染加强**：表格样式美化，代码段高亮以及支持复制。若难以维护，可以考虑迁移为一个独立文档站，通过导航栏与本站链接，使用 vitepress。
- **网页移动端适配**


## 4. 高优先级目标说明

1. **交易与主体状态模型重构（部分未完成）**
	- 交易/账本归档落表（Postgres）：表结构设计、索引与分页查询 API。
	- 同步策略落地：界定 assign/delta 适用边界，完成写穿/双写实现与回退方案。
	- （可选）日/周度快照与回放：用于长周期分析与回归调试。
2. **运行期脚本安全护栏（未完成）**
	- 执行超时：对脚本执行增加 `asyncio.wait_for` 包装，异常路径与回滚处理。
	- 资源隔离：对 CPU 密集任务使用受限线程池/进程池。
	- 失败记录增强：堆栈与上下文信息入库，便于定位问题。

## 5. 协作与知识同步

- **代码审查关注点**：凡触及 `SimulationOrchestrator ↔ DataAccessLayer ↔ ScriptRegistry` 的改动，必须附带接口契约更新与相关测试。
- **Issue 管理**：将高优路线图中的三个子任务拆分 Issue，标记 `priority/high`，并在描述中链接对应文档章节。
- **文档更新节奏**：交付涉及数据模型或 API 变更时，需同步修改第 2、4 章并在 PR 模板中注明。
- **测试守则**：新增功能至少包含 1 个单元测试 + 1 个集成用例；涉及脚本执行需覆盖异常路径。

## 6. 更新指引

- 使用 `docs/dev_handbook/` 作为单一事实来源。
