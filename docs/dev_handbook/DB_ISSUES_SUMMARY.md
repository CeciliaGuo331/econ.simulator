## 数据库相关问题汇总

本文档汇总了在开发与 Docker 部署过程中发现的与数据库（Postgres）和缓存（Redis）相关的问题、成因分析、诊断步骤、短中长期缓解建议与运维检查清单。目标是帮助开发者与运维人员快速定位与修复一致性、性能与可靠性问题。

> 说明：本仓库使用的持久/缓存栈为 Redis（缓存/快速读取）与 PostgreSQL（持久化历史、脚本存储、tick 日志等），应用在读取时优先使用缓存，未命中时回源到持久层并回填缓存（见 `CompositeStateStore` 实现）。脚本元数据同时存在 registry 内存索引与 `scripts` 表的持久表中。

---

## 一、已发现的重要问题（与已施行修复）

1. PostgreSQL 初始化 SQL 字符串中意外嵌入 Python 内容导致语法错误
   - 症状：容器启动时 asyncpg 报错：SQL 语法错误（例如在 CREATE TABLE 处出现 `#` 或 Python 代码片段）。
   - 根因：代码中拼接的 SQL 字符串被误包含了非 SQL 文本。
   - 修复：校正 schema 创建语句，移除嵌入的 Python 片段（见 `script_engine/postgres_store.py` / `data_access` 的 schema 相关实现）。

2. asyncpg 在批量写 tick logs 时出现 TypeError：expected str, got dict
   - 症状：执行批量插入 JSONB 字段时抛出类型错误（asyncpg 需要传入的 JSONB 内容为字符串或适当类型）。
   - 根因：代码将 Python dict 直接传入 executemany/execute 绑定参数，而 asyncpg 对 JSONB 参数需要序列化字符串或使用 proper json adapters。
   - 修复：在 `PostgresTickLogStore.record_many` 中对 `context` 字段执行 `json.dumps`（并在序列化失败时 fallback 为 str），确保传递给 asyncpg 的参数是可接受的类型。

3. 脚本 registry 与 world_state（快照）之间的不一致性与竞态
   - 症状：UI 上出现 world_state.household 数目与脚本挂载数不同步；在 attach 后看到挂载计数 +2（双增）或 attach 成功但实体尚未出现在 world snapshot；reset 后脚本仍显示挂载或实体恢复缓慢（例如需 1 分钟）。
   - 根因：多个因素叠加：attach/register 流程跨越 registry 内存索引、scripts 表持久化与 world_state 的持久化（Redis/Postgres），并且这些步骤在时间上是异步的或被并发执行，造成短期或较长时间的不一致；此外并发 attach 时的 id 分配也可能出现竞态。容器化环境下 Postgres/Redis 性能或延迟会放大问题。
   - 已采取行动：将 `ScriptRegistry.attach_script` 的关键可用性检查与内存索引更新移入 registry 的锁（`_registry_lock`）保护下，并在持久化失败时回滚内存更新以避免双写/重复计数的竞态。

4. reset/seed/attach 启动顺序问题（Docker 特有）
   - 症状：在 Docker 启动流程中，baseline scripts 在某些情况下未正确附加到预期的 simulation，或 startup seed 与应用并发，产生错误日志。命令行下难以复现。
   - 根因：Docker compose 并行启动多个服务（app、postgres、redis），且应用没等待 DB/Redis 完全 ready 即开始 schema 创建或 seed，导致偶发失败或重复尝试。
   - 已采取行动：统一 startup seeding 流程（在 lifespan 中按顺序调用 seed_world，再 attach baseline scripts），并把预期的冲突日志降为 debug 等级以减少噪音。

---

## 二、为什么 Redis 与 Postgres 的不同步会造成混乱（关键机制）

- CompositeStateStore 的读取路径为：先查 Redis（cache），cache miss 则回源到 Postgres（persistent），成功时回填 Redis。
- 脚本挂载涉及两套状态：脚本的绑定（registry 内存索引 + scripts 表）与实体存在（world_state.households）。这两者的更新顺序不同、持久化速度不同，会产生短期不一致。
- 大量并发写入（如 attach、reset、run_tick 触发的 state persist）会触发写锁、队列化与持久层等待，增加延迟，扩大不一致窗口。

结论：Redis/PG 回填延迟、持久化顺序与并发都会导致 UI 看到 world 与 scripts 的不同步；若不加控制，会产生逻辑异常（例如 MissingAgentScriptsError、重复挂载、或在 run_tick 时缺少实体）。

---

## 三、诊断步骤（从快到慢）

1. 快速对比（在应用运行环境的 Python REPL 或小脚本）

```python
from econ_sim.core.orchestrator import SimulationOrchestrator
from econ_sim.script_engine import script_registry
import asyncio

async def inspect(sim_id):
    orch = SimulationOrchestrator()
    ws = await orch.get_state(sim_id)
    scripts = await script_registry.list_scripts(sim_id)
    owners = {m.user_id for m in scripts if m.agent_kind and m.agent_kind.value == 'household'}
    print('world household count:', len(ws.households))
    print('registry household scripts:', [(m.script_id, m.user_id, m.entity_id) for m in scripts if m.agent_kind and m.agent_kind.value=='household'])
    print('distinct owners:', len(owners))

asyncio.run(inspect('your-sim'))
```

2. 测量 reset/回填耗时

```python
import time, asyncio
from econ_sim.core.orchestrator import SimulationOrchestrator

async def measure(sim):
    orch = SimulationOrchestrator()
    t0 = time.time()
    await orch.data_access.reset_simulation(sim)
    t1 = time.time()
    print('reset elapsed:', t1 - t0)
    t2 = time.time()
    _ = await orch.get_state(sim)
    print('get_state after reset elapsed:', time.time() - t2)

asyncio.run(measure('diag-sim'))
```

3. 在 Postgres 层观察慢查询/锁/等待

在 psql 中运行：

```sql
-- 正在运行的活动
SELECT pid, state, query, now() - query_start AS duration FROM pg_stat_activity WHERE state <> 'idle' ORDER BY duration DESC;

-- 锁信息
SELECT relation::regclass, mode, COUNT(*) FROM pg_locks JOIN pg_class ON pg_locks.relation = pg_class.oid GROUP BY relation, mode ORDER BY COUNT(*) DESC;

-- 检查表大小和索引
SELECT relname, n_live_tup, pg_relation_size(relid) FROM pg_stat_user_tables ORDER BY pg_relation_size(relid) DESC LIMIT 20;
```

4. Redis 延迟检查

使用 redis-cli：

```bash
redis-cli --latency
redis-cli --latency-history
```

或在应用运行过程中检查特定 key 的响应时间：删除 key 让应用回源，然后测 get_state 的时间。

5. 检查异步写入点日志

在日志中搜索 `attach`、`save_script`、`_persist_state`、`record_many` 等打点，确认哪个阶段耗时最长或有异常回滚日志。

---

## 四、短期缓解措施（立刻可做）

1. 在 UI 上同时显示两个计数（world snapshot 的 households 与 registry 的 owners），并显示“最后更新时间”。
2. 在 attach API 返回时（或 UI 显示挂载成功时）把实体创建/持久化工作同步化或至少等待 cache 写就绪（trade-off：更高 latency，换取强一致性）。
3. 在 Docker 部署中确保 Postgres/Redis 资源（CPU/IO）充足并尽量避免容器限制过紧；把 Postgres connection pool size 调大到合理值以减少等待（初始建议 10–20，视机器而定）。
4. 在 `PostgresTickLogStore.record_many` 中已做 JSON 序列化修复，避免写入异常影响后续流程。

---

## 五、中期改进（需要改动代码、部署配置）

1. 将 world snapshot 持久化改为增量/分片写（按实体写入而不是每次写整快照），显著降低序列化/IO 成本。
2. 把 `attach`/`allocate entity id` 流程尽量下沉到数据库层（通过序列或 SELECT ... FOR UPDATE），避免在内存读取时产生的 id 分配竞态。
3. 实现异步一致性修复器（periodic reconciler）：对比 `scripts` 表与 world_state，发现悬空挂载或缺少实体的记录并自动或半自动修复。
4. 对持久化与 cache 回填路径增加可观测性：记录阶段性耗时（persist start/end，cache write，pg write，回填完成），上报到监控（Prometheus/日志）。

---

## 六、长期架构建议

1. 事件驱动持久化：把关键写操作（attach、seed、reset）写成事件并送到队列，使用独立 worker 进行持久化与回填；API 返回仅代表“事件已接收”，worker 保证最终一致性并上报状态。这样能把写延迟从请求链路中抽离。
2. 若系统规模进一步增长，考虑使用专门的时间序列/历史存储（或分区策略）来保存 tick logs，减轻主表负载。

---

## 七、已做修复记录（摘要）

- 修复了 PostgreSQL schema 字符串中误嵌入 Python 代码导致的语法错误（CREATE TABLE 修正）。
- 修复了 `PostgresTickLogStore.record_many` 中把 dict 直接传给 asyncpg 的问题，改为 json.dumps 序列化。避免 asyncpg 抛 TypeError。
- 将 `ScriptRegistry.attach_script` 的关键检查与内存索引更新移动到 registry 锁内，并在持久化失败时回滚内存更新，显著降低并发 attach 导致的双计数竞态。
- 在应用启动（lifespan）中，统一执行 `seed_test_world(...)` 后再 `ensure_baseline_scripts(..., attach_to_simulation='test_world')`，并把预期的 singleton attach 冲突日志降级为 debug，减少噪音。

---

## 八、运维检查清单（部署前/疑难时执行）

1. 环境与配置
   - 确认 `ECON_SIM_POSTGRES_DSN`、`ECON_SIM_REDIS_URL` 已正确设置且连通。
   - 检查容器/主机的 CPU、内存、IO 限制（Docker desktop/Moby、Kubernetes limits）。

2. Postgres 健康
   - 检查 `pg_stat_activity` 中长时间运行或等待的事务。
   - 检查锁争用（`pg_locks`）。
   - 检查表与索引大小、是否需要 VACUUM/ANALYZE。

3. Redis 健康
   - 检查 `redis-cli --latency`、`INFO` 输出中的延迟/阻塞情况。

4. 应用层
   - 查看服务日志中与 `_persist_state`、`save_script`、`record_many`、`attach_script` 相关的耗时与异常。
   - 若发现频繁回滚或重试，采集示例请求与相应日志片段以便复现。

5. 参数建议（初始）
   - Postgres pool max size: 10–20（视机器与并发调整）。
   - Redis: 保证低延迟、适当的 maxclients，避免阻塞。 
   - WORKER_MAX_TASKS: 若频繁回收产生高开销，可把值调大（例如 1000），并观察内存泄露情况。

---

## 九、参考（代码位置）

- world snapshot / composite store: `econ_sim/data_access/redis_client.py`
- postgres pool 与重试: `econ_sim/data_access/postgres_support.py`
- tick logs: `econ_sim/data_access/postgres_ticklogs.py`
- script store: `econ_sim/script_engine/postgres_store.py`
- script registry: `econ_sim/script_engine/registry.py`
- baseline seed: `econ_sim/script_engine/baseline_seed.py`
- web dashboard &计数: `econ_sim/web/views.py`

---

如果你愿意，我可以：
- 在当前 Docker 环境中运行一份诊断脚本（测 reset、attach、run 10/100 ticks 的分阶段耗时并收集 pg/redis 状态），将输出贴给你；
- 或先把 `run_day` 增加阶段性耗时日志（不改变行为），便于在生产重放时看到瓶颈。

请选择你更希望我先做的操作（运行诊断脚本 / 打点日志 / 生成运维检查脚本）。

---

## 十、未解决的问题与待办（待实施/验证）

下面列出当前仍然未完全解决或需进一步验证的问题，以及每项建议的优先级与下一步行动要点。将这些项作为短期（S）、中期（M）、长期（L）工作排期参考。

1) 在 Docker 中稳定复现并验证 attach 双计数问题（优先级：高，类型：验证）
   - 说明：我们在代码中修复了 registry 的并发写竞态（将内存更新纳入 lock，并在持久化失败时回滚），但仍需在 Docker/生产样式环境中复现此前观察到的 `attach` 导致计数 +2 的情况并确认修复效果。
   - 下一步：在 Docker compose 环境下并行运行若干 attach 请求并收集服务/DB/Redis 日志（我可以运行并提交结果）。

2) 将 world snapshot 持久化改为按实体增量写（优先级：高，类型：改进，S→M）
   - 说明：目前每次持久化会写整张 world snapshot，导致序列化/IO 成本高，重置或大量实体写入时延迟明显。
   - 下一步：评估并实现 `store_entity` 接口在生产路径中的使用（逐步替换 `_persist_state` 的全量写）。需要编写集成测试并评估一致性窗口。

3) DB 层的原子化 entity id 分配（优先级：高，类型：改进，M）
   - 说明：当前 `_allocate_entity_id` 在内存/应用层完成，有竞态可能。建议将 id 分配下沉到 DB，使用序列或 SELECT ... FOR UPDATE 保证原子性。
   - 下一步：设计 DB 方案（sequence 或锁行），实现并在测试中验证竞态情况。

4) 定期一致性修复器（Reconciler）（优先级：中，类型：新增功能，M）
   - 说明：自动扫描 `scripts` 表与 `world_state` 的差异并自动修复或发出告警，减少手动干预。
   - 下一步：实现轻量的周期任务，先以只读检测为主，再实现自动修复策略（例如 detach 悬空脚本或重新 seed 实体）。

5) run_day / run_tick 的分阶段耗时采集与可视化（优先级：中，类型：可观测性，S）
   - 说明：需要在 orchestrator 的关键相位（script execution, decision merge, market logic, persist）记录耗时并上报指标，帮助定位瓶颈。
   - 下一步：提交小 patch 在 `SimulationOrchestrator` 中打点（Prometheus 或日志），不改变现有行为。

6) 增强 Docker/CI 下的集成回归测试（优先级：中，类型：测试）
   - 说明：目前某些问题仅在容器化环境出现。需要在 CI 或本地 dev 环境中加入 docker-compose 的集成测试，模拟服务启动顺序与资源限制。
   - 下一步：添加 `tests/integration/docker_startup` 脚本在 CI 可选运行，或提供本地 `make ci-docker-test` 目标。

7) Postgres schema 迁移方案（优先级：中，类型：运维/架构）
   - 说明：当前 schema 由应用启动时 `CREATE TABLE IF NOT EXISTS` 管理，建议迁移到显式迁移工具（如 Alembic）以便更可控的版本管理。
   - 下一步：评估并引入 Alembic，迁移初始 schema 并在 CI 中验证回滚/升级。

8) 持久化错误与重试策略统一（优先级：中，类型：可靠性）
   - 说明：对持久化失败（Postgres 写失败、Redis 写失败）应有一致的重试、backoff 和告警流程，避免 silent failure 或只记录日志。
   - 下一步：引入统一的 `persist_with_retry` wrapper（已在部分模块有类似 `run_with_retry`），并把重要持久写调用纳入该 wrapper。

9) sandbox/worker 参数与资源调优（优先级：中，类型：性能）
   - 说明：根据监控调整 `WORKER_MAX_TASKS`、ProcessPool 大小和 `script_execution_concurrency`，避免频繁回收与进程重启。
   - 下一步：制定压测计划，基于实际脚本负载调参并记录基准。

10) 运维脚本和采集模板（优先级：中，类型：运维）
   - 说明：提供标准化的命令集合用于快速收集 pg/redis 状态（pg_stat_activity, pg_locks, redis INFO/latency）和应用日志时间段快照。
   - 下一步：把这些命令整理为 `deploy/tools/collect_db_snapshot.sh` 并加入 README 使用说明。

---

如果你同意，我可以先实现第 1（Docker 环境下复现并验证）或第 5（run_day 打点）中的一项，请告诉我优先选择哪个。完成后我会把结果与下一步计划贴上来。

