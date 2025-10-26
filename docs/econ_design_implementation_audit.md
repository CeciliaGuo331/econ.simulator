# econ_design 实现审计摘要

此文档基于 `docs/econ_design/*.md` 与当前代码库实现（`econ_sim/`）的对比检查，列出主要不一致点、影响与建议修复方案。

## 已完成

- 已读取并比对以下文档：`agent.md`, `interface_contracts.md`, `market.md`, `new_decision_schema.md`, `world_settings.md`。

## 主要不一致项（按优先级）

1) 中央银行政策利率字段命名不一致  (高)
   - 文档：使用 `policy_rate` 或 `policy_rate_t`（多处文档与决策 schema 使用该命名）。
   - 代码：`econ_sim/data_access/models.py` 中 `CentralBankState` 定义了 `base_rate` 字段；而 `CentralBankDecision` 使用 `policy_rate`，`orchestrator` 在将决策写回时使用键 `policy_rate`。
   - 影响：更新中央银行决策后，持久化的字段名可能与模型字段不匹配，造成策略变化未生效或被丢弃。
   - 建议：统一命名（推荐将模型字段改为 `policy_rate` 或在持久化/应用更新时将 `policy_rate` 映射到 `base_rate`）；并添加单元测试覆盖此路径。

2) 世界/仿真时间刻度默认值与文档不同 (中)
   - 文档：`n_ticks_per_day = 100`，并有详细 per-tick 利率转换说明。
   - 代码/配置：`config/world_settings.yaml` 与 `utils/settings.py` 的默认 `ticks_per_day` 为 `3`。
   - 影响：默认仿真粒度不同，可能导致策略在文档中描述的行为与运行结果不一致。
   - 建议：将 `config/world_settings.yaml` 的默认值与文档对齐，或在 docs 明确标注这是可配置并且默认值为 3；并在 README 中说明 per-tick vs 年化利率的关系。

3) 缺少明确的 Agent/Market 接口契约实现（低→中）
   - 文档：`interface_contracts.md` 建议引入 `Agent.observe/decide`、`Market.collect_orders/clear`、以及 Orchestrator 的纯函数式 `execute_tick` 签名。
   - 代码：当前实现通过 `script_registry`、`BaselineFallbackManager` 和 `logic_modules` 分散实现，但仓库中尚无 `econ_sim/core/interfaces.py` 或等价的 Protocol。已添加一个非破坏性的 `econ_sim/core/interfaces.py`（本次改动）。
   - 影响：重构与替换策略实现时缺少统一接口会增加耦合与回归风险。
   - 建议：逐步把 `logic_modules` 的公共入口适配到该接口，先做适配器层保证行为不变，再替换调用方。

4) Household / Firm 状态字段不完全对齐（中）
   - 文档：家户应包括 `cash_t`, `savings_t`, `bond_holdings_t`, `education_level`, `productivity_t`, `is_studying` 等字段；企业应包含 `capital_stock_t`, `debt_t` 等。
   - 代码：`HouseholdState` 使用 `balance_sheet`（含 `cash`, `deposits`, `loans`）以及部分简化字段（如 `skill`、`preference`），缺少 `education_level`、`is_studying`、`expected_income` 等显式字段；`FirmState` 未包含 `capital_stock` 字段。
   - 影响：文档中决策/演化规则无法直接映射到当前模型字段，需编写适配器或扩展模型以保证行为一致。
   - 建议：确认优先级字段并扩展 `HouseholdState` 与 `FirmState`，或在 `entity_factory` 中提供映射/衍生字段以兼容现有逻辑。

5) 商品/劳动力/国债市场实现大体符合文档，但细节存在差异（低）
   - 举例：商品市场在平局价时以 agent id 排序（确定性）；文档建议使用 `rng_seed_global + tick_index` 的随机扰动来打破平局；劳动力撮合、金融市场与国债撮合的主要流程在 `logic_modules` 中已有实现。
   - 建议：若可复现随机性是设计要求，应统一 tie-break 的 RNG 源与 seed 策略；否则补充文档说明当前实现的确定性策略。

## 建议的修复优先级与后续步骤

短期（可快速落地）
- 在 `data_access/models.py` 或 `core/orchestrator.py` 中统一 `policy_rate/base_rate` 命名，或在 `_apply_single_update` 中对 CENTRAL_BANK 的 `policy_rate` 做映射（非破坏性修补）。
- 在 `docs` 或 `config/world_settings.yaml` 明确当前默认 `ticks_per_day=3` 并给出建议值 100 的上下文与影响。
- 为关键字段（如 central bank rate、bank deposit/loan rate、household balance fields）添加单元测试覆盖（happy path + 名称映射边界）。

中期（需少量重构）
- 逐步实现 `econ_sim/core/interfaces.py` 中定义的接口适配器（已新增文件），并把 `logic_modules` 的入口函数包装成实现这些接口的适配器。
- 扩展或补充 `HouseholdState` / `FirmState` 的字段，或在 `core/entity_factory.py` 中提供派生字段以恢复文档中出现的变量（如 education_level、capital_stock）。

长期（可选，较大改动）
- 将 orchestrator 内部流程与 market subsystems 的 contract 更明确化（MarketInterface），并为 market 层添加幂等性与更严格的序列化测试。

## 我可以为你做的事情（可选）

1. 我可以实现“短期”建议中的非破坏性修补（例如：把 `policy_rate` 写入映射到 `base_rate`，并添加对应单元测试）。
2. 我可以把 `docs/econ_design_implementation_audit.md` 扩展为更详细的差异表格，并生成 PR 列出具体的代码修改建议（含 Patch）。
3. 我可以继续逐项执行待办清单并在每步之后运行相关测试以确保不引入回归。

---

当前已完成：读取并初步比对文档与代码、标注主要不一致项并添加了低风险的 `econ_sim/core/interfaces.py` 以便后续重构。

下一步建议：你希望我先修复哪个高优先级项？（建议先修复中央银行字段命名映射）
