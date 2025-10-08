# 经济仿真世界设定

本文件定义仿真世界的跨代理共享参数、时间结构以及宏观指标的计算方式。所有变量均采用 snake_case 代码风格命名，以便在文档与实现之间建立一一对应关系。

---

## 1. 时间刻度与随机性

### 1.1 离散时间结构

* `n_ticks_per_day = 100`: 每个自然日包含的离散事件循环数。
* `tick_index = t ∈ ℕ`: 全局 tick 序号，从 0 开始。
* `day_index = ⌊t / n_ticks_per_day⌋`: 当前 tick 所在的天数，从 0 开始计数。
* `tick_in_day = (t mod n_ticks_per_day) + 1`: 当前 tick 在当天内部的位置，取值范围 `[1, n_ticks_per_day]`。
* `is_daily_decision_tick = (tick_in_day = 1)`: 只有在每日第一个 tick 时，家户可以更新 `is_employed`、`is_studying` 等跨日决策；其他市场交易在所有 tick 上均可发生。

仿真主循环按照 `t` 递增执行，完成 `simulation_days` 个自然日后终止。

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
    * 产出函数：`output_t = technology_t * capital_stock_t**alpha * labor_input_t**(1 - alpha)`，其中 `alpha = 0.33`。
    * **收入与支付子阶段**：如果 `is_daily_decision_tick = True`，企业支付工资 `wage_payment = wage_offer * hours_assigned`，政府发放转移，银行计提利息。
  * **市场交易子阶段**：串行运行劳动力、商品、金融市场撮合与结算（细节见《市场设计》）。
    * 金融市场部分仅包含家户与企业向商业银行的存款、取款和贷款撮合，企业不发行债券或股票。
  * 政府债券认购由家户与商业银行提交订单后采用随机顺序撮合，成交结果用于更新家户 `bond_holdings` 与现金流。购买的国债需持有满一天，到第二天同一 tick 才能提现本金并获得利息。

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
  * `price_index_t = max(ε, λ_cpi * goods_price_t + (1 - λ_cpi) * price_index_{t-1})`
  * `λ_cpi = 0.3`，`ε = 10^{-6}` 防止除零。
* **通胀率**
  * `inflation_rate_t = (price_index_t - price_index_{t-1}) / price_index_{t-1}`，结果裁剪到 `[-0.2, 0.2]`。
* **总产出与 GDP**
  * `aggregate_output_t = firm_output_t`。
  * `gdp_t = goods_price_t * aggregate_output_t + government_spending_t`。
* **失业率**
  * `unemployment_rate_t = 1 - (employed_households_t / household_count)`。
* **产出缺口**
  * `output_gap_t = (aggregate_output_t - potential_output) / potential_output`。
* **平均工资与利率**
  * `average_wage_t = wage_bill_t / max(employed_households_t, 1)`。
  * `average_deposit_rate_t`、`average_loan_rate_t` 为银行发布利率的简单平均。

所有聚合指标将作为 `market_data` 暴露给代理人策略层，用于生成下一 tick 的决策。

---

## 5. 数据记录与可见性

* `agent_state_history`: 每个代理在 tick 结束后的状态快照，仅对系统可见。
* `market_order_log`: 包含各市场所有订单与成交记录，暴露给管理员与回放工具。
* `macro_history`: 记录所有宏观指标时间序列，作为教学可视化与策略评估的基础。

以上设定与《代理人设计》《市场设计》文档构成统一闭环：世界参数限定变量范围，市场机制负责撮合结算，代理人依托这些信息进行决策。实现时应以这些变量名和公式为准，确保文档与代码保持一致。

---

## 6. 外生冲击与随机机制总览

为了保持三份设计文档的一致性，本节汇总所有显式定义的外生冲击、随机初始化以及市场层随机机制，并给出来源。若新增冲击，请同步更新下表。

### 6.1 持续动态类冲击

| 名称 | 分布 / 机制 | 作用范围 | 文档来源 |
| --- | --- | --- | --- |
| 家户生产率扰动 `shock_productivity_t` | `TruncNormal(0, 0.05, -0.2, 0.2)`，逐 tick 抽样 | 改变家户 `productivity_t`，影响劳动供给与消费 | 《代理人设计》1.1 |
| 企业技术冲击 `shock_tech_t` | `TruncNormal(0, 0.03, -0.1, 0.1)`，逐 tick 抽样 | 更新 `technology_t`，直接作用于产出函数 | 《代理人设计》2.1 |
| 家户求职决策噪声 | `Bernoulli(job_search_prob_d)`，每日 tick1 评估 | 决定是否提交劳动订单 | 《代理人设计》1.2 |
| 劳动力匹配噪声 | `matching_score_i = 0.8 * human_capital_score_i + 0.2 * epsilon_i`，`epsilon_i ~ Uniform(0, 1)` | 决定家户录用排序，高人力资本仍占优势 | 《市场设计》4.2 |
| 国债乱序撮合 | 使用 `rng_seed_global + tick_index` 生成随机序列 | 决定 `bond_allocation_t` 分配顺序 | 《市场设计》5.3 |

### 6.2 初始化随机异质性

| 名称 | 分布 | 作用范围 | 文档来源 |
| --- | --- | --- | --- |
| 家户初始资产 `assets_0` | `TruncNormal(100, 15, 60, 160)` | 设定现金与储蓄起点 | 《代理人设计》1.1 |
| 现金占比 `cash_share_0` | `Uniform(0.3, 0.5)` | 切分家户初始现金/储蓄 | 《代理人设计》1.1 |
| 家户能力 `ability` | `TruncNormal(1.0, 0.08, 0.7, 1.3)` | 决定长期人力资本 | 《代理人设计》1.1 |
| 初始教育水平 `education_level_0` | `TruncNormal(0.5, 0.1, 0.2, 0.8)` | 影响生产率与工资预期 | 《代理人设计》1.1 |
| 初始就业状态 `is_employed_0` | `Bernoulli(0.6)` | 控制劳动力市场起点 | 《代理人设计》1.1 |
| 企业资产负债项初值 | 多个 `TruncNormal(...)` 参数 | 塑造企业现金、债务、库存、资本 | 《代理人设计》2.1 |
| 银行资产负债项初值 | `TruncNormal(...)` | 决定 `reserves_t`、`loans_t`、`deposits_t` | 《代理人设计》3.1 |
| 银行不良率 `non_performing_ratio_t` | `Uniform(0.02, 0.05)` | 影响信贷供给与利率定价 | 《代理人设计》3.1 |
| 央行与政府资产项 | `TruncNormal(...)` | 设定宏观部门初始状态 | 《代理人设计》4.1、5.1 |

### 6.3 市场层随机机制与管理端控制

* **商品市场需求加成**：`demand_markup_i ~ TruncNormal(0, 0.05, -0.1, 0.2)`，决定限价单偏离基准价格的幅度（《市场设计》3.1）。
* **商品市场平局打破**：当限价相同时，按 `rng_seed_global + tick_index` 注入噪声排序（《市场设计》3.2），确保成交顺序随机化。
* **管理端外生冲击接口**：在事件循环的观测阶段，保留 `exogenous shock` 控制变量入口，供管理员或脚本注入额外情景（本文件 3）。

> 以上冲击均依赖 `rng_seed_global` 及其派生种子，保持固定种子即可获得可重复的仿真轨迹。
