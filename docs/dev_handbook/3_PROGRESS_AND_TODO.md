# 进度与待办

本章汇总当前成果、质量基线与路线图，让新成员快速了解“平台层 ↔ 仿真世界”双层架构的推进节奏。

## 1. 版本快照（截至 2025-10-08）

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

## 3. TODO

### 数据库
- **每tick交易数据持久化**：当前仅在redis。
- **历史数据查看api向用户开放**：当前仅能查看当前数据。

### 世界设计
- **每日 Tick 批处理**：自动执行按钮（现为执行n day）改为执行1day，输入框中输入一天中tick的数量。
- **日终脚本轮换**：每日n tick交易全部结束之后支持修改脚本（卸载原脚本，上传新脚本并继承状态与数据）。
- **考虑加入每日定时自动执行n tick的功能**

### 经济系统设计

- **经济模型重构**：当前市场和各种主体的设计未遵循文档中的经济设计，仅作平台测试用，需要重构。

### 用户
- **LLM API封装**：封装api，提供安全的模型接口。限制调用次数。
- **经济模型重构后平台策略脚本相关api需要相应修改，以及更新网站文档页**

### 网页
- **数据渲染加强**
- **网页性能优化**：优化并发。
- **用户个人主页**：在页面右上角点击头像进入个人主页。支持修改头像、邮箱、用户名、密码。支持通知功能，用于接收系统通知。
- **仪表盘功能拆分**
- **网页上的用户文档补充**：用户编写脚本时，阅读网站上的文档应当能获得足够多信息。
- **文档页渲染加强**：表格样式美化，代码段高亮以及支持复制。若难以维护，可以考虑迁移为一个独立文档站，通过导航栏与本站链接，使用vitepress。
- **网页移动端适配**


## 4. 高优先级目标说明

1. **每日 Tick 调度 & 日终脚本换代**
	- 在 `SimulationOrchestrator` 引入 `run_day_plan(days: int, ticks_per_day: int)`。
	- Day 结束触发 `ScriptRegistry.rotate_scripts(simulation_id, household_overrides)`，实现旧脚本卸载 + 新脚本挂载 + 状态迁移。
	- API 层新增 `/simulations/{id}/schedule/day-run`（计划）用于后台任务触发；Web 增加日终策略上传入口。
2. **交易与主体状态模型重构**
	- 扩展 Redis `sim:{id}:state` 结构以容纳交易撮合、账户流水。
	- PostgreSQL 引入 `simulation_limits` 之外的 `script_versions`、`agent_snapshots`（草案），与未来状态持久化路线保持一致。
	- 更新数据同步策略：界定 `assign` / `delta` 的使用场景，准备写穿或双写方案。
3. **运行期脚本安全护栏**
	- 对 `ScriptRegistry` 执行增加 `asyncio.wait_for` 超时包装；对 CPU 密集任务引入受限线程池。
	- 失败脚本记录增强：落表 + 详细报错。

## 5. 协作与知识同步

- **代码审查关注点**：凡触及 `SimulationOrchestrator ↔ DataAccessLayer ↔ ScriptRegistry` 的改动，必须附带接口契约更新与相关测试。
- **Issue 管理**：将高优路线图中的三个子任务拆分 Issue，标记 `priority/high`，并在描述中链接对应文档章节。
- **文档更新节奏**：交付涉及数据模型或 API 变更时，需同步修改第 2、4 章并在 PR 模板中注明。
- **测试守则**：新增功能至少包含 1 个单元测试 + 1 个集成用例；涉及脚本执行需覆盖异常路径。

## 6. 更新指引

- 使用 `docs/dev_handbook/` 作为单一事实来源。