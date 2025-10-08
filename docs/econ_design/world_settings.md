# 经济仿真世界设定

本文件定义仿真世界的跨代理共享参数、时间结构以及宏观指标的计算方式。所有变量均采用 snake_case 代码风格命名，以便在文档与实现之间建立一一对应关系。

---

## 1. 时间刻度与随机性

### 1.1 离散时间结构

* `n_ticks_per_day = 100`: 每个自然日包含的离散事件循环数。
* `tick_index = τ ∈ ℕ`: 全局 tick 序号，从 0 开始。
* `day_index = ⌊τ / n_ticks_per_day⌋`: 当前 tick 所在的天数，从 0 开始计数。
* `tick_in_day = (τ mod n_ticks_per_day) + 1`: 当前 tick 在当天内部的位置，取值范围 `[1, n_ticks_per_day]`。
* `is_daily_decision_tick = (tick_in_day = 1)`: 只有在每日第一个 tick 时，家户可以更新 `is_employed`、`is_studying` 等跨日决策；其他市场交易在所有 tick 上均可发生。

仿真主循环按照 `τ` 递增执行，完成 `simulation_days` 个自然日后终止。

### 1.2 随机数设定

* `rng_seed_global`: 全局随机数生成器种子，默认值为 `42`。所有代理在初始化时使用 `rng_seed_global + agent_id` 作为局部种子，以确保可复现性。
* `TruncNormal(μ, σ, lower, upper)`: 截断正态分布，先从 `N(μ, σ²)` 取样，再将样本裁剪到 `[lower, upper]`。
* `Uniform(a, b)`: 连续均匀分布。
* `Bernoulli(p)`: 伯努利分布，概率 `p` 取值 1。

---

## 2. 全局常量与默认参数

| 变量名 | 默认值 | 取值范围 / 类型 | 描述 |
| --- | --- | --- | --- |
| `household_count` | 400 | 正整数 (≥1) | 初始家户数量 |
| `firm_count` | 1 | 正整数 | 初始企业数量 |
| `bank_count` | 1 | 正整数 | 初始商业银行数量 |
| `government_exists` | True | 布尔 | 是否启用政府代理 |
| `central_bank_exists` | True | 布尔 | 是否启用央行代理 |
| `simulation_days` | 365 | 正整数 | 仿真天数上限 |
| `price_index_base` | 1.0 | (0, +∞) | 基期价格水平 |
| `potential_output` | 120.0 | (0, +∞) | 潜在产出 (以商品市场数量计) |
| `discount_factor` | 0.96 | (0, 1) | 家户贴现因子 `β` |
| `risk_aversion` | 2.0 | (0, +∞) | 家户 CRRA 效用函数风险厌恶度 `σ` |
| `phi_inflation` | 1.5 | (0, +∞) | 泰勒规则对通胀偏差的权重 |
| `phi_output` | 0.5 | (0, +∞) | 泰勒规则对产出缺口的权重 |
| `reserve_ratio_base` | 0.08 | [0, 1) | 基础法定准备金率 |
| `policy_rate_base` | 0.02 | [0, 0.5) | 基准年化政策利率 |
| `loan_rate_spread_base` | 0.03 | [0, 0.5) | 银行贷款利率对政策利率的基本加点 |
| `deposit_rate_spread_base` | -0.01 | (-0.5, 0.5) | 银行存款利率对政策利率的基本贴点 |
| `labor_search_base_prob` | 0.35 | [0, 1] | 家户在失业状态下的基线求职概率 |
| `education_cost_per_day` | 2.0 | [0, +∞) | 家户投入教育的每日现金成本 |
| `education_gain` | 0.05 | (0, 1] | 每完成一次教育周期的人力资本增量 |
| `wage_base` | 1.2 | (0, +∞) | 基线名义工资 |
| `depreciation_rate` | 0.05 | [0, 1) | 企业资本折旧率 (年化) |
| `inventory_carry_cost` | 0.01 | [0, 0.5) | 企业库存单位持有成本 |

所有利率和产出默认以年化名义值给出。引擎在每个 tick 内通过 `effective_rate_per_tick = (1 + annual_rate)^(1 / (n_ticks_per_day * 365)) - 1` 将其转换为等效的 tick 利率。

---

## 3. 标准事件循环

每个 tick 按以下顺序执行，确保世界状态在阶段之间保持一致：

1. **观测阶段 (Observation)**
    * 所有代理收集当前可见信息：
        * 私有状态 `agent_state`。
        * 公开市场数据 `market_data`（见《市场设计》文档）。
        * 管理端设定的 exogenous shock 控制变量。

2. **计划阶段 (Planning)**
  * 家户在 `is_daily_decision_tick` 时生成 `consumption_plan`, `labor_plan`, `education_plan`，其中 `education_plan` 仅在当日 tick1 可重新选择是否投入教育。
    * 企业计算 `production_plan`, `labor_demand_plan`, `price_plan`。
    * 银行和政府设定当期的报价或政策变量。

3. **执行阶段 (Execution)**
    * **生产子阶段**：企业根据 `production_plan` 和上一 tick 的 `labor_assignment` 更新 `inventory` 与 `capital_stock`。
        * 产出函数：$\text{output}_τ = technology_τ \cdot capital_{τ}^{\alpha} \cdot labor_{τ}^{1-\alpha}$，其中 `α = 0.33`。
    * **收入与支付子阶段**：如果 `is_daily_decision_tick = True`，企业支付工资 `wage_payment = wage_offer * hours_assigned`，政府发放转移，银行计提利息。
  * **市场交易子阶段**：串行运行劳动力、商品、金融市场撮合与结算（细节见《市场设计》）。
    * 金融市场部分仅包含家户与企业向商业银行的存款、取款和贷款撮合，企业不发行债券或股票。
    * 政府债券认购由家户与商业银行提交订单后采用随机顺序撮合，成交结果用于更新家户 `bond_holdings` 与现金流。

4. **结算阶段 (Settlement)**
    * 更新所有余额、资产、库存以及债务。
    * 记录成交日志与价格。

5. **统计阶段 (Statistics)**
    * 计算宏观指标 `price_index`, `inflation_rate`, `unemployment_rate` 等。
    * 将结果写入系统可见的 `macro_snapshot`。

---

## 4. 宏观指标计算

以下聚合变量在每个 tick 的统计阶段更新：

* **价格指数**
  * `price_index_τ = max(ε, λ_cpi * goods_price_τ + (1 - λ_cpi) * price_index_{τ-1})`
  * `λ_cpi = 0.3`，`ε = 10^{-6}` 防止除零。
* **通胀率**
  * `inflation_rate_τ = (price_index_τ - price_index_{τ-1}) / price_index_{τ-1}`，结果裁剪到 `[-0.2, 0.2]`。
* **总产出与 GDP**
  * `aggregate_output_τ = firm_output_τ`。
  * `gdp_τ = goods_price_τ * aggregate_output_τ + government_spending_τ`。
* **失业率**
  * `unemployment_rate_τ = 1 - (employed_households_τ / household_count)`。
* **产出缺口**
  * `output_gap_τ = (aggregate_output_τ - potential_output) / potential_output`。
* **平均工资与利率**
  * `average_wage_τ = wage_bill_τ / max(employed_households_τ, 1)`。
  * `average_deposit_rate_τ`、`average_loan_rate_τ` 为银行发布利率的简单平均。

所有聚合指标将作为 `market_data` 暴露给代理人策略层，用于生成下一 tick 的决策。

---

## 5. 数据记录与可见性

* `agent_state_history`: 每个代理在 tick 结束后的状态快照，仅对系统可见。
* `market_order_log`: 包含各市场所有订单与成交记录，暴露给管理员与回放工具。
* `macro_history`: 记录所有宏观指标时间序列，作为教学可视化与策略评估的基础。

以上设定与《代理人设计》《市场设计》文档构成统一闭环：世界参数限定变量范围，市场机制负责撮合结算，代理人依托这些信息进行决策。实现时应以这些变量名和公式为准，确保文档与代码保持一致。
