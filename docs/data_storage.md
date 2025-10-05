# 数据存储与结构说明

本文档面向需要了解 `econ.simulator` 数据持久化与结构设计的开发者，重点说明系统当前维护的“表”（或集合）、字段以及它们之间的关系。虽然默认实现主要基于内存/Redis JSON 文档，但表结构的描述同样适用于未来迁移到关系型数据库的场景。

## 1. 数据类别概览

| 数据类别 | 说明 | 默认实现 | 可选/拟议持久化 | 主要维护模块 |
| -------- | ---- | -------- | ---------------- | ------------ |
| 仿真世界状态 | 每个仿真实例的世界快照（Tick/Day、各主体状态） | `InMemoryStateStore`（内存） | `RedisStateStore`（键值 JSON） | `econ_sim.data_access` |
| 仿真实例参与者 | 记录加入同一仿真实例的用户 ID | 内存集合 | 可扩展为 Redis Set | `econ_sim.data_access.DataAccessLayer` |
| 用户账号 | 登录凭证、用户类型、管理员种子账号 | `InMemoryUserStore` | `RedisUserStore` Hash | `econ_sim.auth.user_manager` |
| 登录会话 | 访问令牌 → 邮箱映射 | Session 内存字典 | 可扩展为 Redis Hash/Set | `econ_sim.auth.user_manager.SessionManager` |
| 自定义脚本 | 用户上传的策略脚本与元数据 | `ScriptRegistry` 内存字典 | 拟扩展为 Redis/数据库 | `econ_sim.script_engine.registry` |

## 2. 仿真世界状态表

世界状态是调度器与逻辑模块协作的核心数据。无论使用内存还是 Redis，系统都会以完整 JSON 文档的形式存储该表。

- **存储键：**
  - 内存：`InMemoryStateStore._storage[simulation_id]`。
  - Redis：`econ_sim:sim:{simulation_id}:world_state`（字符串，值为 JSON）。
- **主键：**`simulation_id`。

### 2.1 world_state 表结构

| 字段 | 类型 | 说明 | 关系 |
| ---- | ---- | ---- | ---- |
| `simulation_id` | `str` | 仿真实例唯一标识 | 关联 `world_participants.simulation_id`、`scripts.simulation_id` |
| `tick` | `int` | 当前 Tick 序号 | 与 `macro` 指标联动，驱动调度循环 |
| `day` | `int` | 当前日序号 | 控制每日多 Tick 的业务逻辑 |
| `households` | `Dict[int, household_state]` | 家户主体集合 | 与企业/政府/银行通过雇佣、税收、贷款建立联系 |
| `firm` | `firm_state` | 企业主体唯一实例 | 其 `employees` 列表指向家户 ID |
| `government` | `government_state` | 政府主体 | `employees` 列表指向家户 ID |
| `bank` | `bank_state` | 商业银行主体 | `approved_loans` 指向家户 ID |
| `central_bank` | `central_bank_state` | 央行政策参数 | 影响银行利率/准备金 |
| `macro` | `macro_state` | GDP、通胀等宏观指标 | 聚合所有主体数据，无直接外键 |

### 2.2 household_state（嵌套结构）

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `id` | `int` | 家户 ID，与字典键一致 |
| `balance_sheet` | `BalanceSheet` | 现金、存款、贷款、库存 |
| `skill` | `float` | 劳动力技能 |
| `preference` | `float` | 消费/储蓄偏好 |
| `employment_status` | `Enum` | 就业状态（失业/企业雇佣/政府雇佣） |
| `employer_id` | `Optional[str]` | 当前雇主（如 `firm_1`、`government`） |
| `wage_income` | `float` | 上一 Tick 工资收入 |
| `labor_supply` | `float` | 劳动供给量 |
| `last_consumption` | `float` | 上一 Tick 消费额 |
| `reservation_wage` | `float` | 接受雇佣的保留工资 |

`BalanceSheet` 包含 `cash`、`deposits`、`loans`、`inventory_goods` 四个数值字段。

### 2.3 firm_state

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `id` | `str` | 企业 ID，默认 `firm_1` |
| `balance_sheet` | `BalanceSheet` | 企业现金、存款、库存 |
| `price` | `float` | 当前商品价格 |
| `wage_offer` | `float` | 工资报价 |
| `planned_production` | `float` | 计划产出 |
| `productivity` | `float` | 生产率参数 |
| `employees` | `List[int]` | 雇佣的家户 ID，与 `households` 形成一对多 |
| `last_sales` | `float` | 上一 Tick 销售额 |

### 2.4 government_state

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `id` | `str` | 默认 `government` |
| `balance_sheet` | `BalanceSheet` | 财政状况 |
| `tax_rate` | `float` | 当前税率 |
| `unemployment_benefit` | `float` | 失业救济标准 |
| `spending` | `float` | 政府支出预算 |
| `employees` | `List[int]` | 政府雇员 ID，与家户形成一对多 |

### 2.5 bank_state

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `id` | `str` | 默认 `bank` |
| `balance_sheet` | `BalanceSheet` | 现金、存款、贷款情况 |
| `deposit_rate` | `float` | 存款利率 |
| `loan_rate` | `float` | 贷款利率 |
| `approved_loans` | `Dict[int, float]` | 已批准的贷款额度，键为家户 ID |

### 2.6 central_bank_state

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `id` | `str` | 默认 `central_bank` |
| `base_rate` | `float` | 基准利率 |
| `reserve_ratio` | `float` | 法定准备金率 |
| `inflation_target` | `float` | 通胀目标 |
| `unemployment_target` | `float` | 失业目标 |

### 2.7 macro_state

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `gdp` | `float` | 国内生产总值估计 |
| `inflation` | `float` | 通胀率 |
| `unemployment_rate` | `float` | 失业率 |
| `price_index` | `float` | 价格指数 |
| `wage_index` | `float` | 工资指数 |

> **关系梳理：**
>
> - `households.employer_id` ↔ `firm.id` / `government.id`。
> - `firm.employees` 与 `government.employees` 均引用家户 ID。
> - `bank.approved_loans` 以家户 ID 为键。
> - `macro` 指标来源于其他主体的统计聚合，不包含外键。

## 3. 仿真实例参与者表

`DataAccessLayer` 在内存中维护一个参与者集合，可映射为 `world_participants` 表：

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `simulation_id` | `str` | 主键，与 `world_state.simulation_id` 对应 |
| `participants` | `Set[str]` | 参与者邮箱/用户 ID 集合 |

目前该表仅用于记录共享仿真实例的用户，未来可以扩展为 Redis Set（键：`econ_sim:sim:{id}:participants`）。

## 4. 用户与会话表

### 4.1 users

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `email` | `str` | 主键，所有邮箱会被规整为小写去除空格 |
| `password_hash` | `str` | PBKDF2 哈希结果 |
| `created_at` | `datetime` | 注册时间（ISO8601） |
| `user_type` | `str` | 用户类型：`individual` / `firm` / `government` / `commercial_bank` / `central_bank` / `admin` |

- **默认实现：**`InMemoryUserStore._users`。
- **Redis 实现：**Hash `{prefix}:users`（默认 `econ_sim:users`），field=邮箱，value=上述字段的 JSON。

### 4.2 sessions

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `token` | `str` | 主键，使用 UUID4 生成 |
| `email` | `str` | 外键，对应 `users.email` |

- **默认实现：**`SessionManager._tokens`（内存字典），受 `asyncio.Lock` 保护。
- **扩展建议：**迁移到 Redis 后，可使用 Hash `econ_sim:sessions` 或 `SETEX token email` 结合过期时间。

## 5. 自定义脚本表

`ScriptRegistry` 可以类比为一张 `scripts` 表：

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `script_id` | `str` | 主键，UUID4 |
| `simulation_id` | `str` | 外键，指向 `world_state.simulation_id` |
| `user_id` | `str` | 外键，指向 `users.email` |
| `description` | `Optional[str]` | 脚本描述 |
| `created_at` | `datetime` | 上传时间（UTC） |
| `func` | `Callable` | 编译后的脚本函数，仅内存保存 |

目前脚本代码与元数据存放在内存中：`ScriptRegistry._scripts[simulation_id][script_id]`。若要持久化，建议：

1. 在 Redis Hash `econ_sim:scripts:{simulation_id}` 中保存元数据 JSON（含 `user_id`、`description`、`created_at`、`code`）。
2. 将脚本源码/附件存入对象存储或数据库，再通过 URL/哈希关联。

## 6. 实现细节：内存 vs Redis

| 表/集合 | 内存实现 | Redis 实现 | 备注 |
| ------- | -------- | ---------- | ---- |
| `world_state` | `InMemoryStateStore._storage` | `econ_sim:sim:{id}:world_state` | JSON 文档，读写由 `DataAccessLayer` 负责 |
| `world_participants` | `DataAccessLayer._participants` | （可选）`econ_sim:sim:{id}:participants` | 当前仅内存 |
| `users` | `InMemoryUserStore._users` | `{prefix}:users` Hash | `UserManager` 自动种子化管理员账号 |
| `sessions` | `SessionManager._tokens` | 尚未实现，推荐 Hash 或 `SETEX` | 需自定义过期策略 |
| `scripts` | `ScriptRegistry._scripts` | 尚未实现，推荐 Hash + 对象存储 | 仅保存函数引用，进程退出丢失 |

## 7. 迁移与扩展建议

1. **集中配置**：使用环境变量/配置文件统一声明 Redis 地址、Session Secret、管理员默认密码等。
2. **数据迁移脚本**：为 `users`、`world_state` 提供导入/导出工具，便于从内存迁移到 Redis 或关系型数据库。
3. **会话持久化**：多实例部署时将 `sessions` 迁移到共享存储，并引入过期及刷新策略。
4. **脚本持久化**：若需长期保存用户脚本，建议引入版本号、审核状态等字段，并使用专用存储。
5. **审计与备份**：为关键表增加操作日志，定期备份 Redis/数据库。

## 8. 关联文档

- **《[数据模型与访问层设计](code_structure/2_DATA_MODEL.md)》**：介绍 Pydantic 模型定义与数据访问流程。
- **《[开发者部署指南](deployment.md)》**：涵盖启用 Redis、配置 Session Secret 的操作步骤。
- **`docs/api/` 文档**：了解用户注册、仿真控制等 API 如何消费上述数据结构。

通过以上设计，`econ.simulator` 在保持轻量内存实现的同时，也明确了各数据表的字段与关系，为未来的持久化与扩展打下基础。

## 9. Redis + PostgreSQL 重构目标

选择 “Redis + PostgreSQL” 组合后，我们将 Redis 作为高频读写的实时缓存层，PostgreSQL 作为权威持久层。以下目标用于指导逐步重构：

### 9.1 架构抽象
- **统一接口层**：扩展 `DataAccessLayer`，通过配置同时实例化 `RedisStateStore` 与即将新增的 `PostgresStateStore`，暴露读写接口而不泄露底层差异。
- **写入策略**：对仿真世界状态采用写穿（write-through）策略，保证 Redis 写入成功后立即持久化到 PostgreSQL；低频数据（用户、脚本元数据）可使用写回队列。
- **读取策略**：优先命中 Redis；缓存未命中时，从 PostgreSQL 回源并回填缓存，确保单一代码路径即可完成读流程。

### 9.2 数据建模
- **PostgreSQL 架构**：设计 `world_state_snapshots`、`participants`, `users`, `scripts`, `sessions` 等表；使用 JSONB 字段存储世界状态快照，关系型字段维护索引与约束。
- **迁移脚本**：提供 Alembic（或同类工具）迁移，包含表结构、索引、默认值；同时准备数据导入脚本，从 Redis 批量同步到 PostgreSQL。
- **一致性元数据**：为 Redis 键设计与表名一致的命名规范（如 `econ_sim:sim:{id}:world_state` ↔ `world_state_snapshots.simulation_id`）。

### 9.3 同步与后台任务
- **刷新调度**：实现独立的异步任务（如 `state_flush_worker`），在写回模式下定期从 Redis 消费变更队列批量写入 PostgreSQL，并记录回执。
- **事件追踪**：为关键写操作记录事件表或使用消息队列（Outbox Pattern），以便审计与重放。
- **故障恢复**：定义启动流程：若 Redis 丢失数据，则自动从 PostgreSQL 最新快照回填；反之若 Postgres 临时不可用，则在 Redis 内部排队等待重试。

### 9.4 运维支持
- **配置统一化**：在 `.env` 或设置模块中补充 PostgreSQL 连接串、连接池参数、Redis 集群/哨兵配置，并在 `deployment.md` 中记录部署步骤。
- **监控告警**：接入指标（缓存命中率、写入延迟、重试次数），输出到 Prometheus/OpenTelemetry；设置慢查询与队列积压告警。
- **备份策略**：制定 PostgreSQL 定期备份与 Redis 快照（RDB/AOF）策略，文档化恢复流程。

### 9.5 迭代与测试
- **兼容性阶段**：保持内存实现作为开发 fallback；通过特性标志（feature flag）允许逐步切换到 Redis + PostgreSQL。
- **测试矩阵**：新增集成测试，覆盖缓存命中/未命中、写穿失败回退、并发加入仿真等场景；在 CI 中引入 Docker Compose 或 Testcontainers 启动 Redis & PostgreSQL。
- **性能基线**：制定基准测试（大规模 Tick 更新、上百并发用户加入），记录当前指标与重构后改进幅度。

通过以上目标，团队可以分阶段落地 Redis + PostgreSQL 架构，兼顾实时响应能力与强一致的持久化需求，同时为未来的横向扩展与运维自动化打下基础。
