# 脚本 API 总览（合并版）

目的：汇总并简化当前脚本执行相关的所有 API、数据传递（context）与主要实现位置，便于协作者在短期维运期间快速理解如何编写策略脚本、现有能力与局限。

要点速览
- Registry（`econ_sim/script_engine/registry.py`）负责准备并裁剪 `world_state`，构造脚本执行的 `context` 并调用沙箱执行。
- Sandbox（`econ_sim/script_engine/sandbox.py`）负责安全执行脚本（模块白名单、内建函数限制、超时/资源限制、进程池或子进程执行）。
- user_api（`econ_sim/script_engine/user_api.py`）主要是决策构造与数值工具库（OverridesBuilder, clamp, fraction, moving_average），不主动查询持久层或其他实体。

文档结构
1. 脚本入口与返回类型
2. context（脚本可见数据）
3. user_api 功能一览
4. 已实现 / 未实现（简洁版）
5. 短期建议（低成本改动）
6. 关键源码位置

---

1) 脚本入口与返回类型
- 脚本必须定义：
```python
def generate_decisions(context):
    # 返回能被 TickDecisionOverrides 校验的 dict
    return {...}
```
- 返回结构以 `TickDecisionOverrides` 为目标（见 `econ_sim/data_access/models.py`）

2) context（脚本可见数据）
- `context` 字段：
  - `world_state`：裁剪后的世界视图（含 `tick`, `day`, `features`, `macro`，以及针对脚本可见的实体片段）
  - `entity_state`：该脚本绑定实体的完整序列化状态
  - `config`：world config 的 JSON 表示
  - `script_api_version`：当前为 1
  - `agent_kind`, `entity_id`

- 裁剪规则（registry）简述：
  - household 脚本：`world_state['households']` 仅包含该脚本绑定的家户（entity_id）
  - firm/bank/government/central_bank：对应字段仅在 id 匹配时包含实体，否则为 None

3) user_api 功能一览（已实现）
- 文件：`econ_sim/script_engine/user_api.py`
- 主要 API：
  - `OverridesBuilder`：.household(...).firm(...).bank(...).government(...).central_bank(...).build()
  - 工具函数：`clamp`, `fraction`, `moving_average`
- 说明：这些工具仅用于在脚本内构造决策与做数值计算，不进行跨实体查询或持久层访问

4) 已实现 / 未实现（最小化陈述）
- 已实现：脚本沙箱执行、context 传递与裁剪、决策构造器与基础数值工具、脚本注册时的静态检查（AST）。
- 未实现：任何面向脚本的、可主动拉取更广世界数据的 helper（例如 `get_public_market_data(context)`、`query_households`、server-side 查询代理）。

5) 短期建议（优先级排序）
- 优先（低风险）：在 `user_api.py` 添加只读 helper（基于传入 `context` 做聚合/抽取），例如 `get_public_market_data(context)`, `get_entity_state(context)`。不访问持久层即可满足大多数需求。
- 次优（中风险）：在 registry 中提供可选裁剪扩展（管理员开关），例如把部分 public market 数据直接注入 `context['world_state']`。
- 长期（高投入）：设计 server-side `script_api`，注入可审计的只读调用点（含权限与 rate-limit）。

6) 关键源码位置（快速定位）
- Registry / context 构造： `econ_sim/script_engine/registry.py`（方法 `_execute_script` / `generate_overrides`）
- 沙箱执行： `econ_sim/script_engine/sandbox.py`（`execute_script`, `_run_in_subprocess`, `ALLOWED_MODULES`）
- 决策工具： `econ_sim/script_engine/user_api.py`
- Orchestrator 调用点： `econ_sim/core/orchestrator.py::run_tick`
- 数据模型（TickDecisionOverrides 等）： `econ_sim/data_access/models.py`

---

备注：我已把更详细的状态文档合并入此文件并删除旧版冗余文档（若需要我可保留旧稿备份）。

需要我现在把 `user_api.py` 扩展出 2-3 个只读 helper（并附带示例脚本与 tests）吗？如果是，我会把该任务加入 TODO 并开始实现。