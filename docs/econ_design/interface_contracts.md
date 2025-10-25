# 接口契约草案（重构指导）

本文件作为重构初期的契约草案，用以把 `docs/econ_design` 中的概念映射到代码实现并作为抽象层/适配器开发的蓝图。

目标：
- 定义简洁明确的接口（输入/输出/副作用），便于替换底层实现且最小化回归风险；
- 给出数据形状示例（与 `data_access.models` 对齐）；
- 列出关键边界条件、错误模式与性能假设。

---

## 1 快速契约总结（2-4 条）

- Agent（Household/Firm/Bank/Government/CentralBank）公开 2 类方法：`observe(world_state) -> Observation` 与 `decide(observation) -> Decision`，决策为纯数据对象（Pydantic/Dataclass），不进行 IO。
- Market 层提供 `collect_orders(decisions) -> Orders` 与 `clear(orders, world_state) -> (StateUpdates, MarketLogs)`，并以幂等方式返回变更指令（不直接写入持久层）。
- Orchestrator 负责事件循环、脚本覆盖合并、调用 Agent/Market 接口并协调持久化；其 contract 保证每次 Tick 的执行顺序与返回值（TickResult）稳定。
- WorldConfig/WorldState 作为只读输入传入各接口，任何需要持久化的变更必须由返回的 StateUpdateCommand 列表统一提交。

---

## 2 数据形状（示例，参考 `data_access.models`）

- Observation (输入给 Agent)
  - world_tick: int
  - market_data: dict (goods_price, wage_offer, deposit_rate, loan_rate, price_index, inflation_rate, unemployment_rate, ...)
  - agent_state: dict (该 agent 的私有状态快照)

- Decision (Agent -> Orchestrator)
  - HouseholdDecision: {consumption_budget: float, savings_rate: float, labor_supply: float, bond_bid: Optional[List[BondBid]], deposit_order: float, withdrawal_order: float}
  - FirmDecision: {planned_production: float, hiring_demand: int, wage_offer: float, price: float}
  - BankDecision: {deposit_rate: float, loan_rate: float, loan_offers: List[LoanRequest]}
  - GovernmentDecision/CentralBankDecision: policy vars

- Orders / MarketInputs
  - GoodsOrder = List[ {agent_id, quantity, limit_price} ]
  - LaborOrder = {labor_offers: List[...] , labor_demands: List[...] }
  - FinanceOrders = {deposits: List[...], withdrawals: List[...], loan_requests: List[...], bond_bids: List[...]}

- StateUpdateCommand (输出用于持久化)
  - assign(kind, agent_id, **fields)  # 与现有 StateUpdateCommand 格式兼容

---

## 3 Python 风格接口草案（示例）

建议在 `econ_sim/core/contracts.py` 或 `econ_sim/core/interfaces.py` 中定义这些接口：

- Agent 接口 (伪代码)

    class AgentInterface(Protocol):
        def observe(self, world_state: WorldState) -> Observation: ...
        def decide(self, observation: Observation) -> Decision: ...

- Market 接口 (伪代码)

    class MarketInterface(Protocol):
        def collect_orders(self, decisions: TickDecisions, world_state: WorldState) -> Orders: ...
        def clear(self, orders: Orders, world_state: WorldState) -> Tuple[List[StateUpdateCommand], MarketLog]: ...

- Orchestrator contract (高阶函数签名)

    def execute_tick(world_state: WorldState, decisions: TickDecisions, config: WorldConfig) -> Tuple[List[StateUpdateCommand], List[TickLogEntry]]:
        """纯函数式：不直接持久化，仅返回需要写回的更新与日志供调用方提交。"""

实现建议：现有 `logic_modules.market_logic.execute_tick_logic` 与 `orchestrator.SimulationOrchestrator.run_tick` 已部分遵循此契约，可作为参考。

---

## 4 边界条件与错误模式

- 决策对象必须可序列化（用于脚本覆盖合并与持久化），避免在 Decision 中携带复杂不可序列化引用。
- Market 的 clear() 实现应保证幂等：相同输入应产生相同 StateUpdateCommand 列表。
- Orchestrator 在持久化失败时应保证可重试性（幂等写入或事务回滚）；短期内不进行自动补救。
- 性能假设：单个 tick 的市场清算应在 <50ms（单线程逻辑）为目标；若超出需拆分撮合器以并行/分片处理。

---

## 5 小结与下一步建议

1. 将上述接口定义成代码（`econ_sim/core/interfaces.py`），并在 `logic_modules` 与 `core` 中逐步改写调用点以依赖接口而非具体实现。
2. 在第一阶段（低风险）实现“适配器层”：编写适配器把现有 `execute_tick_logic`、`collect_tick_decisions` 等包装为符合新接口的适配器，保持外部行为不变。
3. 为接口添加单元测试（mock world_state + baseline decisions），保证适配器输出与现有函数一致。

如果你同意，我将：
- 在仓库中添加 `econ_sim/core/interfaces.py`（空接口/Protocol）并为现有 `execute_tick_logic` 和 `collect_tick_decisions` 添加小型适配器（不改变行为）；
- 同时添加对应的单元测试（tests/test_interfaces.py：happy path + 一个边界 case）。

---

文件由自动化任务生成，作为重构起点。后续我会继续实现第一阶段的适配器与测试，除非你希望先调整接口契约。