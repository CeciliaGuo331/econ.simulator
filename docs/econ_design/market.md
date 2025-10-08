# 市场设计

本文件阐述《世界设定》中标准事件循环的市场阶段，定义商品、劳动力与金融市场的订单结构、竞价算法以及状态更新规则。所有变量名称与《代理人设计》保持一致。

---

## 1. 数据可见性与接口

### 1.1 可见性层级

* **层级 0 — `agent_state`**：仅个体可见的私有状态（现金、库存等）。
* **层级 1 — `market_data`**：对所有代理公开的聚合指标与当前报价，如 `goods_price`, `wage_offer`, `deposit_rate` 等。
* **层级 2 — 系统数据**：用于回放与监控的完整订单簿、成交日志，不向策略层暴露。

| 层级 | 数据类型 / 接口 | 典型字段 | 来源与更新时间 | 可见主体 |
| --- | --- | --- | --- | --- |
| 0 (`agent_state`) | 家户状态快照（见《代理人设计》1.1～1.3） | `cash_t`, `savings_t`, `bond_holdings_t`, `ability`, `is_employed_d`, `reservation_wage_d` | 由家户策略在观测/执行阶段写入；每个 tick 更新一次 | 对应家户代理自身 |
| 0 (`agent_state`) | 企业/银行/政府/央行内部状态（见《代理人设计》2～5） | 企业：`inventory_t`, `goods_price_t`, `wage_offer_t`；银行：`reserves_t`, `loan_rate_t`；政府：`gov_cash_t`, `tax_rate_income_t`；央行：`policy_rate_t`, `reserve_ratio_t` | 各主体在计划与执行阶段维护；tick 级更新 | 各主体自身；其他代理不可见 |
| 1 (`market_data`) | 市场撮合后的价格与聚合指标（见本文件 3、4 及《世界设定》4） | `goods_price_t`, `aggregate_demand_t`, `excess_demand_t`, `wage_offer_d`, `deposit_rate_t`, `loan_rate_t`, `inflation_rate_t`, `unemployment_rate_t` | 市场撮合器与统计阶段汇总；每个 tick 广播 | 所有策略代理共享 |
| 1 (`market_data`) | 运行参数与世界常量（《世界设定》1～3） | `n_ticks_per_day`, `tick_in_day`, `simulation_days`, `potential_output`, `policy_rate_base` | 仿真初始化写入，必要时由央行/政府政策函数在执行阶段调整 | 所有策略代理共享 |
| 2 (`market_order_log`) | 各市场订单与成交日志（见本文件 2～5） | `goods_order`, `labor_offer`, `labor_demand`, `deposit_order`, `loan_request`, `bond_bid`, `clearing_price`, `fill` | 市场撮合完成后追加；tick 级持久化 | 仅系统监控、管理员与回放工具可见 |
| 2 (`agent_state_history`) | 全量状态时间序列（《世界设定》5） | 与层级 0 相同的完整状态，按 tick 存档 | 结算阶段批量写入 | 系统内控与调试工具 |
| 2 (`macro_history`) | 宏观统计历史（《世界设定》4～5） | `price_index_t`, `gdp_t`, `credit_growth_t`, `average_wage_t`, `average_deposit_rate_t` | 统计阶段计算后写入；每日及 tick 级采样 | 系统监控、教学可视化、回测框架 |

> 参考文档：`docs/econ_design/agent.md`、`docs/econ_design/world_settings.md` 与本文件其他章节，保证数据接口与实现保持一致。

### 1.2 订单数据结构

| 市场 | 订单类型 | 字段 | 约束 |
| --- | --- | --- | --- |
| 商品市场 | `goods_order` | `agent_id`, `quantity`, `limit_price`, `timestamp` | `quantity ≥ 0`, `limit_price ≥ 0.1` |
| 劳动力市场 | `labor_offer`, `labor_demand` | `reservation_wage`, `productivity`, `slots` | 每日 `tick_in_day = 1` 执行 |
| 金融市场 | `deposit_order`, `withdrawal_order`, `loan_request`, `bond_bid` | 依次包含金额、期限、利率需求 | 金融订单在所有 tick 允许提交 |

系统在执行阶段汇总所有订单并调用对应市场撮合器，返回成交结果数组写入 `market_order_log`。

---

## 2. 市场执行时间线

| 阶段 | Tick 条件 | 操作 |
| --- | --- | --- |
| 观测 | 任意 | 拉取 `market_data`，准备订单 |
| 计划 | 任意 | 生成各类订单对象 |
| 劳动力市场 | `tick_in_day = 1` | 匹配 `labor_offer` 与 `labor_demand` |
| 商品市场 | 任意 | 处理 `goods_order`，更新成交与库存 |
| 金融市场 | 任意 | 处理 `deposit`, `withdraw`, `loan`, `bond` |

劳动力市场先于商品、金融市场执行，使当日工资结果能影响消费与融资决策。

---

## 3. 商品市场：集合竞价 + 库存约束

### 3.1 需求生成

* 家户在计划阶段提交 `goods_order_i = (agent_id, quantity_i, limit_price_i)`，其中：
    * `quantity_i = floor(consumption_nominal_i_t / goods_price_t)`，最小为 0。
    * `limit_price_i = goods_price_t * (1 + demand_markup_i)`，`demand_markup_i ~ TruncNormal(0, 0.05, -0.1, 0.2)`。
* 企业提供单一卖单：`supply_order = (firm_id, inventory_t, goods_price_t)`。

### 3.2 竞价算法

1. 将所有买单按 `limit_price` 从高到低排序，若价格相同则使用随机扰动 (seed = `rng_seed_global + tick_index`) 打破平局。
2. 从排序列表头部逐一匹配，直到企业库存耗尽：
     * 成交数量 `fill = min(quantity_i, remaining_inventory)`。
     * 若 `limit_price_i ≥ goods_price_t` 则成交，成交价 `clearing_price = max(goods_price_t, limit_price_next)`，其中 `limit_price_next` 为首个未成交订单的价格，若无则使用 `goods_price_t`。

### 3.3 状态更新

* 家户：`cash_{t+1} -= clearing_price * fill`，`utility_{t+1}` 基于真实消费更新。
* 企业：`cash_{t+1} += clearing_price * fill`，`inventory_{t+1} -= fill`，`goods_sold_t = Σ fill`。
* 聚合指标：
    * `aggregate_demand_t = Σ quantity_i`。
    * `excess_demand_t = (aggregate_demand_t - goods_sold_t)/max(inventory_t, 1)`，供企业在下一个 tick 调整价格。

---

## 4. 劳动力市场：价优择高 + 生产力加权

仅在 `tick_in_day = 1` 执行。

### 4.1 订单提交

* 家户 (求职者)：
    * `labor_offer_i = (agent_id, reservation_wage_i, productivity_i)`。
    * 若 `is_studying_d = 1`，则不提交；若 `is_employed_{d-1} = 1` 但希望续约，也需重新提交。

* 企业 (招聘者)：
    * `labor_demand = (firm_id, slots, wage_offer_d)`，`slots = ceil(production_plan_d / labor_efficiency)`。

### 4.2 匹配流程

1. 构建候选集合，过滤掉 `reservation_wage_i > wage_offer_d * 1.1` 的订单。
2. 企业仅提供单一岗位。撮合器根据家户人力资本水平计算排序所需的权重：
    * 汇总候选集，若集合为空则直接结束。
    * 计算 `productivity_mean = mean(productivity_i)` 并生成 `human_capital_score_i = clip(0.4 + 0.6 * (productivity_i / max(productivity_mean, 0.1)), 0.1, 2.0)`。
    * 为每位候选人生成 `epsilon_i ~ Uniform(0, 1)`（随机数种子 `rng_seed_global + tick_index + agent_id`），并计算 `matching_score_i = 0.8 * human_capital_score_i + 0.2 * epsilon_i`。
3. 按 `matching_score_i` 从高到低排序并依次分配岗位，直至填满 `slots` 或候选用尽；若超出 `slots`，截断后续候选。
4. 成功匹配者更新状态：`is_employed_d = 1`，失败者设置为 0。

### 4.3 工资结算

* 工资在同一 tick 的“执行阶段”发放：`wage_payment = wage_offer_d * labor_hours`，`labor_hours = 1` 默认为全职。
* `labor_assignment_t` 保存到下一 tick 的生产函数中使用。

---

## 5. 金融市场：存贷款与国债

金融市场在每个 tick 执行，包含三个子市场，顺序为存款 → 贷款 → 国债。

### 5.1 存款与取款

* 家户/企业根据流动性目标生成订单：
    * `deposit_order = (agent_id, amount)`，`amount = deposit_flow_t`。
    * `withdrawal_order = (agent_id, amount)`，`amount = withdrawal_flow_t`。
* 银行处理顺序：
    1. 扣除取款，更新 `reserves_t -= amount`，`deposits_t -= amount`。
    2. 添加存款，更新 `reserves_t += amount`，`deposits_t += amount`。
* 利息按 tick 利率逐步计提：`interest_accrual = savings_t * deposit_rate_t^{tick}`。

### 5.2 信贷审批

* 企业与家户提交 `loan_request = (agent_id, principal, desired_rate, purpose)`。
* 银行依据以下条件审批：
    * 若 `principal > loan_supply_t`，按比例缩减。
    * 若 `desired_rate < loan_rate_t^{annual}`，将订单搁置。
    * 违约风险评分：`credit_score = clip(0.6 * collateral_ratio + 0.4 * income_ratio, 0, 1)`，低于 0.3 拒绝。
* 成功审批：
    * 借款人 `cash_{t+1} += approved_principal`，`loan_balance_{t+1} += approved_principal`。
    * 银行 `loans_{t+1} += approved_principal`，`reserves_{t+1} -= approved_principal`。
    * 利息随 tick 增长：`loan_interest_t = loan_balance_t * loan_rate_t^{tick}`。

### 5.3 国债认购

* 政府发布 `bond_issue = (volume, coupon_rate)`。
* 家户与商业银行均可提交 `bond_bid = (agent_id, face_value, bid_price)`，企业不参与本市场。
* 撮合算法：
    1. 汇总所有有效订单后进行随机乱序（使用 `rng_seed_global + tick_index` 生成的可复现随机排列）。
    2. 按乱序结果迭代分配国债面额，直至 `volume` 被消化或订单耗尽；若某订单需求大于剩余额度，则按剩余额度部分成交。
    3. 成交通知用于更新 `bond_allocation_t` 以及现金支出。
* 结算规则：
    * 在某日的某个 tick（如 tick_in_day = k）购买的国债，需持有满一天，到第二天同一 tick（tick_in_day = k）时才能提现本金并计息。
    * 利息计算：`bond_coupon = coupon_rate / 365 * nominal_value`，在提现时一次性发放到 `bond_cashflow_t`。

---

## 6. 市场与宏观指标的联动

* 商品市场成交价格写入 `goods_price_t`，用于更新 `price_index_t` 与 `inflation_rate_t`。
* 劳动力市场匹配结果决定 `employed_households_d`，影响 `unemployment_rate_t`。
* 金融市场的贷款余额直接写入 `loans_t`，供央行计算 `credit_growth_t` 与商业银行的 `capital_adequacy_t`。
* 所有成交量和价格均写入 `market_order_log` 与 `macro_history`，供后续分析和策略回测使用。
