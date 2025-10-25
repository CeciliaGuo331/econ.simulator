# 新的 Decision / Observation Schema（草案）

本文件给出重写后经济逻辑所采用的最小可用决策与观测数据结构草案（基于 `econ_sim/data_access/models.py`）。

目的：
- 在实现新逻辑前固定 I/O contract，减少实现中对接口频繁修改的需要；
- 为 baseline、玩家脚本与 web 渲染提供统一参考。

核心类型（摘录）

- Observation:
  - world_tick: int
  - market_data: PublicMarketData (goods_price, wage_offer, deposit_rate, loan_rate, tax_rate, unemployment_rate, inflation)
  - agent_state: 对应主体的 State（HouseholdState / FirmState / BankState / ...）

- HouseholdDecision:
  - labor_supply: float
  - consumption_budget: float
  - savings_rate: float

- FirmDecision:
  - price: float
  - planned_production: float
  - wage_offer: float
  - hiring_demand: int

- BankDecision:
  - deposit_rate: float
  - loan_rate: float
  - loan_supply: float

- GovernmentDecision:
  - tax_rate: float
  - government_jobs: int
  - transfer_budget: float

- CentralBankDecision:
  - policy_rate: float
  - reserve_ratio: float

实现提示：请直接使用 `econ_sim.data_access.models` 中的 Pydantic 类型以提高校验与一致性。

下一步：基于此 schema 实现一个临时 baseline stub 并把它用于最小事件循环的 smoke 测试。
