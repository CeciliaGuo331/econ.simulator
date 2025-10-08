# 代理人设计

本文件定义所有核心代理人的状态变量、初始化分布、决策函数与约束条件，并与《世界设定》《市场设计》保持一致。除特别说明外，所有时间下标均指全局 tick `t`，相关记号见《世界设定》。

---

## 0. 通用符号与函数

* `TruncNormal(μ, σ, lower, upper)`：截断正态分布。
* `effective_rate(r_annual) = (1 + r_annual)^{1 / (365 * n_ticks_per_day)} - 1`：将年化利率转为 tick 利率。
* `clip(x, a, b)`：将 `x` 裁剪到 `[a, b]`。

---

## 1. 家户代理 (Household Agent)

### 1.1 状态变量

* `cash_t`
    * 初始化：`assets_0 ~ TruncNormal(100, 15, 60, 160)`，`cash_share_0 ~ Uniform(0.3, 0.5)`，`cash_0 = assets_0 * cash_share_0`。
    * 动态：
        $$cash_{t+1} = cash_t + wage\_income_t + transfer\_income_t + loan\_draw_t + bond\_cashflow_t - consumption\_nominal_t - deposit\_flow_t - loan\_repayment_t - education\_cost_t$$
    * 约束：`cash_t ≥ 0`。

* `savings_t`
    * 初始化：`savings_0 = assets_0 - cash_0`。
    * 动态：
        $$savings_{t+1} = (savings_t + deposit\_flow_t - withdrawal\_flow_t) \cdot (1 + deposit\_rate_t^{tick})$$
        其中 `deposit_rate_t^{tick} = effective_rate(deposit_rate_t^{annual})`。
    * 约束：`savings_t ≥ 0`。

* `bond_holdings_t`
    * 初始化：`bond_holdings_0 = 0`。
    * 动态：
        $$bond\_holdings_{t+1} = bond\_holdings_t + bond\_allocation_t - bond\_redemption_t$$
        其中 `bond_allocation_t` 来源于国债市场的成交结果，`bond_redemption_t` 表示到期兑付的面额数量。

* `bond_cashflow_t`
    * 定义：`bond_cashflow_t = coupon_income_t + redemption_cash_t - bond_purchase_payment_t`，由国债市场结算阶段提供。

* `assets_t = cash_t + savings_t + bond_holdings_t`，`assets_t ≥ 0`。

* `ability`
    * 静态异质性：`ability ~ TruncNormal(1.0, 0.08, 0.7, 1.3)`。

* `education_level_t`
    * 初始化：`education_level_0 ~ TruncNormal(0.5, 0.1, 0.2, 0.8)`。
    * 动态：
        $$education\_level_{t+1} = clip(education\_level_t + education\_gain \cdot is\_studying_d,\ 0,\ 1.5)$$
        仅在 `is_daily_decision_tick` 更新，其中 `is_studying_d ∈ {0, 1}`。

* `productivity_t`
    * `shock_productivity_t ~ TruncNormal(0, 0.05, -0.2, 0.2)`。
    * 公式：`productivity_t = ability * (1 + 0.6 * education_level_t) * (1 + shock_productivity_t)`。

* `is_employed_d ∈ {0, 1}`
    * 初始化：`Bernoulli(0.6)`。
    * 仅在每日第一个 tick 可通过劳动力市场匹配更新。

* `reservation_wage_d`
    * 公式：`reservation_wage_d = wage_base * (0.6 + 0.4 * ability) * (1 + 0.5 * (education_level_d - 0.5))`。

* `expected_income_d`
    * 使用自回归预测：`expected_income_d = 0.7 * realized_income_{d-1} + 0.3 * wage_base`。

### 1.2 决策变量

* `consumption_nominal_t`
    * 以 CRRA 效用最大化为目标：
        $$u(c\_{real,t}) = \frac{c\_{real,t}^{1 - risk\_aversion} - 1}{1 - risk\_aversion},\quad c\_{real,t} = \frac{consumption\_nominal_t}{price\_index_t}$$
    * 近似决策规则：
        $$consumption\_nominal_t = clip(κ\_c \cdot (cash_t + savings_t + expected\_income_d)^{θ_c},\ c_{min},\ cash_t + savings_t)$$
        默认 $κ_c = 0.25, θ_c = 0.9, c_{min} = 0.1$。

* `labor_supply_d`
    * 求职概率：
        $$job\_search\_prob_d = clip(labor\_search\_base\_prob + 0.15 \cdot (1 - assets_d / 120),\ 0, 1)$$
    * 若 `is_studying_d = 0` 且 `Bernoulli(job_search_prob_d) = 1`，则提交劳动订单。

* `is_studying_d`
    * 决策窗口：仅在 `is_daily_decision_tick = True` (每日 tick1) 时可重新选择。
    * 选择规则：若 `assets_d > education_cost_per_day * 20` 且 `expected_wage_gain_d > education_cost_per_day`，则设为 1；否则 0。

* `deposit_flow_t` 与 `withdrawal_flow_t`
    * 保持目标流动性比：`target_liquidity_ratio = 0.4`。
    * 若 `cash_t / assets_t > target_liquidity_ratio + 0.1`，则 `deposit_flow_t = cash_t - target_liquidity_ratio * assets_t`。
    * 若 `cash_t / assets_t < target_liquidity_ratio - 0.1`，则提款弥补差额。

* `bond_bid_t`
    * 所有家户可在金融市场阶段任意 tick 提交国债购买订单，形式为 `bond_bid_t = (agent_id, face_value, bid_price)`，默认每个订单对应一日期国债面值单位。
    * 购买的国债需持有满一天，到第二天同一 tick 才能提现本金并获得利息。
    * 出价策略示例：`bid_price = clip(1 - 0.5 * (assets_t / 200 - 0.5), 0.95, 1.05)`，`face_value` 受可用现金与风险偏好约束。

### 1.3 预算与约束

* 预算约束：`assets_{t+1} = assets_t + income_t + bond_cashflow_t - consumption_nominal_t - education_cost_t`。
* 生存约束：`consumption_nominal_t ≥ c_min`。
* 时间约束：`is_employed_d + is_studying_d ≤ 1`。

---

## 2. 企业代理 (Firm Agent)

### 2.1 状态变量

* `cash_t`：初始化 `TruncNormal(200, 40, 120, 400)`。
* `debt_t`：初始化 `TruncNormal(80, 20, 40, 140)`。
* `inventory_t`：初始化 `TruncNormal(60, 10, 30, 100)`。
* `capital_stock_t`：初始化 `TruncNormal(150, 25, 80, 220)`。
* `technology_t`
    * 初始值 `1.0`，动态：`technology_{t+1} = technology_t * (1 + shock_tech_t)`。
    * `shock_tech_t ~ TruncNormal(0, 0.03, -0.1, 0.1)`。

### 2.2 生产与库存

* 生产函数：`output_t = technology_t * capital_stock_t**alpha * labor_input_t**(1 - alpha)`，其中 `alpha = 0.33`
    * `technology_t` 表示企业当前的全要素生产率，体现外生技术冲击。
    * `capital_stock_t**alpha` 是资本投入的贡献，`alpha = 0.33` 代表资本的产出弹性。
    * `labor_input_t**(1 - alpha)` 表示劳动力投入的贡献；`labor_input_t` 可按“有效劳动”度量，即 `sum(productivity_i)`，将人力资本高的员工折算为更多的有效劳动量。
    * 该 Cobb-Douglas 结构意味着资本与劳动力都有递减边际产出，并允许技术水平对总产出做乘性放大。
* 库存动态：
    `inventory_{t+1} = inventory_t + output_t - goods_sold_t - spoilage_t`
    其中 `spoilage_t = 0.01 * inventory_t`。

### 2.3 招聘与岗位编制

* 企业仅提供一种岗位类型，岗位容量由生产计划驱动：`desired_headcount_d = ceil(production_plan_d / labor_efficiency)`。
* 在每日 `tick_in_day = 1` 之前提交劳动力订单：`labor_demand_d = (firm_id, desired_headcount_d, wage_offer_d)`，字段与市场撮合规则保持一致。
* 市场撮合阶段，候选家户根据其 `productivity_i` 生成 `human_capital_score_i`，与随机扰动 `epsilon_i ~ Uniform(0, 1)` 线性组合为 `matching_score_i = 0.8 * human_capital_score_i + 0.2 * epsilon_i`。撮合器按 `matching_score_i` 从高到低排序，企业无需额外排序逻辑。
* 企业将排序结果前 `desired_headcount_d` 名家户写入 `employed_workers_d` 与 `labor_assignment_t`，在下一 tick 的生产函数中使用。
* 若未能填满 `desired_headcount_d`，企业记录缺口比例 `labor_shortage_ratio_t = (desired_headcount_d - employed_workers_d) / max(desired_headcount_d, 1)`，供定价与融资模块引用。

### 2.4 定价与订单

* `goods_price_t`
    * 决策规则：
        $$goods\\_price_{t+1} = clip(goods\\_price_t \cdot (1 + κ_p \cdot excess\\_demand_t),\ 0.5, 5.0)$$
        `κ_p = 0.4`，`excess_demand_t = (aggregate_demand_t - inventory_t)/\max(inventory_t, 1)`。

* `wage_offer_t`
    * 公式：`wage_offer_t = wage_base * (1 + 0.2 * labor_shortage_ratio_t)`。
    * `labor_shortage_ratio_t = clip((labor_demand_t - employed_workers_t)/max(labor_demand_t, 1), -0.5, 0.5)`。

### 2.5 投资与融资

* 资本更新：`capital_stock_{t+1} = (1 - depreciation_rate_tick) * capital_stock_t + investment_t`
    * `depreciation_rate_tick = 1 - (1 - depreciation_rate_annual)**(1 / (365 * n_ticks_per_day))` 表示每个 tick 的折旧比例。
    * `(1 - depreciation_rate_tick) * capital_stock_t` 代表上一期资本在折旧后的剩余量。
    * `investment_t` 是当期新增投资，使得资本存量能够扩充或对冲折旧。
* 投资决策：`investment_t = clip(κ_i * (desired_capital_t - capital_stock_t), 0, cash_t)`，`κ_i = 0.3`。
* 贷款需求：若 `cash_t < payroll_requirement_t + investment_t`，申请 `loan_request_t = payroll_requirement_t + investment_t - cash_t`。
* 资金来源约束：企业仅通过自有现金或商业银行存贷款调节流动性，不发行债券或股票，也不参与政府债券投资。

### 2.6 约束

* 偿付能力 `cash_t + inventory_t * goods_price_t + capital_stock_t - debt_t ≥ 0`。
* 工资义务：在每日第一个 tick 必须支付 `payroll_requirement_d = wage_offer_d * employed_workers_d`。

---

## 3. 商业银行代理 (Commercial Bank Agent)

### 3.1 状态变量

* `reserves_t`：初始化 `TruncNormal(150, 30, 80, 220)`。
* `loans_t`：初始化 `TruncNormal(300, 50, 180, 420)`。
* `deposits_t`：初始化 `TruncNormal(360, 60, 220, 520)`。
* `equity_t = reserves_t + loans_t - deposits_t`。
* `non_performing_ratio_t`：初始化 `Uniform(0.02, 0.05)`。

### 3.2 利率与报价


* 存款利率：
    `deposit_rate_t^{annual} = clip(policy_rate_t + deposit_rate_spread_base - 0.5 * (capital_adequacy_target - capital_adequacy_t), -0.02, 0.1)`
    
    - 其中：
        - `policy_rate_t`：当前央行政策利率。
        - `deposit_rate_spread_base`：银行对存款利率的基础加点，反映市场竞争和银行策略。
        - `capital_adequacy_t`：银行当前资本充足率，`capital_adequacy_t = equity_t / max(loans_t, 1)`。
        - `capital_adequacy_target`：监管或银行自身设定的目标资本充足率，默认 `0.12`。
        - `clip(x, a, b)`：将 `x` 裁剪到 `[a, b]` 区间，防止极端值。
    - 经济含义：当银行资本充足率低于目标时，存款利率会下调（惩罚项为负），以抑制负债扩张、鼓励资本补充；反之则可适度上调。
    - 区间限制：最低 `-2%`，最高 `10%`。

* 贷款利率：
    `loan_rate_t^{annual} = clip(policy_rate_t + loan_rate_spread_base + 0.5 * max(0, capital_adequacy_target - capital_adequacy_t), 0, 0.3)`
    
    - 其中：
        - `loan_rate_spread_base`：银行对贷款利率的基础加点。
        - 其余参数同上。
        - `max(0, capital_adequacy_target - capital_adequacy_t)`：仅当资本充足率低于目标时，贷款利率才有额外上浮，反映风险溢价。
    - 经济含义：资本充足率不足时，银行通过提高贷款利率来补偿风险和资本成本，抑制信贷扩张。
    - 区间限制：最低 `0%`，最高 `30%`。

其中 `capital_adequacy_t = equity_t / max(loans_t, 1)`，目标比率 `capital_adequacy_target = 0.12`。

### 3.3 信贷供给

* 新贷款审批量：
    $$loan\_supply_t = \max\left(0, \frac{reserves_t - reserve\_requirement_t}{1 + non\_performing\_ratio_t}\right)$$
* 准备金要求：`reserve_requirement_t = reserve_ratio_t * deposits_t`。
* 股东红利：若 `equity_t > equity_target`，发放 `dividend_t = 0.2 * (equity_t - equity_target)`，其中 `equity_target = 0.08 * loans_t`。

### 3.4 约束

* `actual_reserve_ratio_t = reserves_t / deposits_t ≥ reserve_ratio_t`。
* `capital_adequacy_t ≥ 0.08`。若不满足，则暂停发放新贷款。

---

## 4. 央行代理 (Central Bank Agent)

### 4.1 状态变量

* `policy_rate_t`：初始化 `policy_rate_base`。
* `reserve_ratio_t`：初始化 `reserve_ratio_base`。
* `central_bank_assets_t`：初始化 `TruncNormal(500, 80, 300, 700)`。
* `central_bank_liabilities_t`：初始化 `central_bank_assets_t`。

### 4.2 政策反应函数

* 泰勒规则：
    $$policy\_rate_{t+1} = clip(policy\_rate\_base + \phi\_inflation \cdot (inflation\_rate_t - target\_inflation) + \phi\_output \cdot output\_gap_t,\ 0, 0.4)$$
    其中 `target_inflation = 0.02`。

* 准备金率调整：
    $$reserve\_ratio_{t+1} = clip(reserve\_ratio_t + 0.1 \cdot (credit\_growth_t - credit\_target),\ 0.05, 0.2)$$
    `credit_growth_t = (loans_t - loans_{t-1})/max(loans_{t-1}, 1)`，`credit_target = 0.03`。

* 公开市场操作：若 `inflation_rate_t > target_inflation + 0.02`，出售国债 `bond_sales_t = 0.05 * central_bank_assets_t`；若低于 `target_inflation - 0.02`，则购入同规模国债。

---

## 5. 政府代理 (Government Agent)

### 5.1 状态变量

* `gov_cash_t`：初始化 `TruncNormal(150, 30, 80, 220)`。
* `gov_debt_t`：初始化 `TruncNormal(200, 40, 100, 320)`。
* `tax_rate_income_t`：初始化 `0.15`。
* `tax_rate_consumption_t`：初始化 `0.05`。
* `government_spending_t`：初始化 `20.0`。

### 5.2 财政规则

* 税率调节：
    $$tax\_rate\_income_{t+1} = clip(tax\_rate\_income_t + 0.1 \cdot (debt\_ratio_t - debt\_target),\ 0.1, 0.35)$$
    `debt_ratio_t = gov_debt_t / max(gdp_t, 1)`，`debt_target = 0.6`。

* 支出规则：
    $$government\_spending_{t+1} = clip(\bar{G} + 0.4 \cdot unemployment\_gap_t,\ 10, 40)$$
    `\bar{G} = 20`，`unemployment_gap_t = unemployment_rate_t - 0.05`。

* 转移支付：`transfer_payment_d = 5 * unemployed_households_d`，在每日第一个 tick 发放。

* 国债发行：若 `fiscal_balance_t = tax_revenue_t - government_spending_t < 0`，发行 `bond_issue_t = -fiscal_balance_t`，利率 `bond_rate_t = policy_rate_t + 0.01`。

### 5.3 约束

* 预算：`gov_cash_{t+1} = gov_cash_t + tax_revenue_t + bond_issue_t - government_spending_t - transfer_payment_t`。
* 债务可持续性：`gov_debt_t / gdp_t ≤ 0.9`，若超过，强制压缩支出 `government_spending_{t+1} = max(government_spending_t - 5, 10)`。

---

## 6. 变量映射与对接

* 所有 `*_t` 变量在 tick 结束后写入 `agent_state_history`。
* 家户、企业、银行的报价变量会在市场阶段作为订单提交，其他代理通过 `market_data` 获取宏观参数。
* 若新增变量，请同时在《世界设定》《市场设计》更新对应的初始化、范围与聚合方式。
