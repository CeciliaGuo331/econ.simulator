# 代理人设计

本文件定义所有核心代理人的状态变量、初始化分布、决策函数与约束条件，并与《世界设定》《市场设计》保持一致。除特别说明外，所有时间下标均指全局 tick `τ`，相关记号见《世界设定》。

---

## 0. 通用符号与函数

* `TruncNormal(μ, σ, lower, upper)`：截断正态分布。
* `effective_rate(r_annual) = (1 + r_annual)^{1 / (365 * n_ticks_per_day)} - 1`：将年化利率转为 tick 利率。
* `clip(x, a, b)`：将 `x` 裁剪到 `[a, b]`。

---

## 1. 家户代理 (Household Agent)

### 1.1 状态变量

* `cash_τ`
    * 初始化：`assets_0 ~ TruncNormal(100, 15, 60, 160)`，`cash_share_0 ~ Uniform(0.3, 0.5)`，`cash_0 = assets_0 * cash_share_0`。
    * 动态：
        $$cash_{τ+1} = cash_τ + wage_income_τ + transfer_income_τ + loan_draw_τ + bond_cashflow_τ - consumption_nominal_τ - deposit_flow_τ - loan_repayment_τ - education_cost_τ$$
    * 约束：`cash_τ ≥ 0`。

* `savings_τ`
    * 初始化：`savings_0 = assets_0 - cash_0`。
    * 动态：
        $$savings_{τ+1} = (savings_τ + deposit_flow_τ - withdrawal_flow_τ) \cdot (1 + deposit_rate_τ^{tick})$$
        其中 `deposit_rate_τ^{tick} = effective_rate(deposit_rate_τ^{annual})`。
    * 约束：`savings_τ ≥ 0`。

* `bond_holdings_τ`
    * 初始化：`bond_holdings_0 = 0`。
    * 动态：
        $$bond\_holdings_{τ+1} = bond\_holdings_τ + bond\_allocation_τ - bond\_redemption_τ$$
        其中 `bond_allocation_τ` 来源于国债市场的成交结果，`bond_redemption_τ` 表示到期兑付的面额数量。

* `bond_cashflow_τ`
    * 定义：`bond_cashflow_τ = coupon_income_τ + redemption_cash_τ - bond_purchase_payment_τ`，由国债市场结算阶段提供。

* `assets_τ = cash_τ + savings_τ + bond_holdings_τ`，`assets_τ ≥ 0`。

* `ability`
    * 静态异质性：`ability ~ TruncNormal(1.0, 0.08, 0.7, 1.3)`。

* `education_level_τ`
    * 初始化：`education_level_0 ~ TruncNormal(0.5, 0.1, 0.2, 0.8)`。
    * 动态：
        $$education\_level_{τ+1} = clip(education\_level_τ + education\_gain \cdot is\_studying_d,\ 0,\ 1.5)$$
        仅在 `is_daily_decision_tick` 更新，其中 `is_studying_d ∈ {0, 1}`。

* `productivity_τ`
    * `shock_productivity_τ ~ TruncNormal(0, 0.05, -0.2, 0.2)`。
    * 公式：`productivity_τ = ability * (1 + 0.6 * education_level_τ) * (1 + shock_productivity_τ)`。

* `is_employed_d ∈ {0, 1}`
    * 初始化：`Bernoulli(0.6)`。
    * 仅在每日第一个 tick 可通过劳动力市场匹配更新。

* `reservation_wage_d`
    * 公式：`reservation_wage_d = wage_base * (0.6 + 0.4 * ability) * (1 + 0.5 * (education_level_d - 0.5))`。

* `expected_income_d`
    * 使用自回归预测：`expected_income_d = 0.7 * realized_income_{d-1} + 0.3 * wage_base`。

### 1.2 决策变量

* `consumption_nominal_τ`
    * 以 CRRA 效用最大化为目标：
        $$u(c\_{real,τ}) = \frac{c\_{real,τ}^{1 - risk\_aversion} - 1}{1 - risk\_aversion},\quad c\_{real,τ} = \frac{consumption\_nominal_τ}{price\_index_τ}$$
    * 近似决策规则：
        $$consumption\_nominal_τ = clip(κ\_c \cdot (cash_τ + savings_τ + expected\_income_d)^{θ_c},\ c\_{min},\ cash_τ + savings_τ)$$
        默认 `κ_c = 0.25`, `θ_c = 0.9`, `c_min = 0.1`。

* `labor_supply_d`
    * 求职概率：
        $$job\_search\_prob_d = clip(labor\_search\_base\_prob + 0.15 \cdot (1 - assets_d / 120),\ 0, 1)$$
    * 若 `is_studying_d = 0` 且 `Bernoulli(job_search_prob_d) = 1`，则提交劳动订单。

* `is_studying_d`
    * 决策窗口：仅在 `is_daily_decision_tick = True` (每日 tick1) 时可重新选择。
    * 选择规则：若 `assets_d > education_cost_per_day * 20` 且 `expected_wage_gain_d > education_cost_per_day`，则设为 1；否则 0。

* `deposit_flow_τ` 与 `withdrawal_flow_τ`
    * 保持目标流动性比：`target_liquidity_ratio = 0.4`。
    * 若 `cash_τ / assets_τ > target_liquidity_ratio + 0.1`，则 `deposit_flow_τ = cash_τ - target_liquidity_ratio * assets_τ`。
    * 若 `cash_τ / assets_τ < target_liquidity_ratio - 0.1`，则提款弥补差额。

* `bond_bid_τ`
    * 所有家户可在金融市场阶段提交国债购买订单，形式为 `bond_bid_τ = (agent_id, face_value, bid_price)`，默认每个订单对应一日期国债面值单位。
    * 出价策略示例：`bid_price = clip(1 - 0.5 * (assets_τ / 200 - 0.5), 0.95, 1.05)`，`face_value` 受可用现金与风险偏好约束。

### 1.3 预算与约束

* 预算约束：`assets_{τ+1} = assets_τ + income_τ + bond_cashflow_τ - consumption_nominal_τ - education_cost_τ`。
* 生存约束：`consumption_nominal_τ ≥ c_min`。
* 时间约束：`is_employed_d + is_studying_d ≤ 1`。

---

## 2. 企业代理 (Firm Agent)

### 2.1 状态变量

* `cash_τ`：初始化 `TruncNormal(200, 40, 120, 400)`。
* `debt_τ`：初始化 `TruncNormal(80, 20, 40, 140)`。
* `inventory_τ`：初始化 `TruncNormal(60, 10, 30, 100)`。
* `capital_stock_τ`：初始化 `TruncNormal(150, 25, 80, 220)`。
* `technology_τ`
    * 初始值 `1.0`，动态：`technology_{τ+1} = technology_τ * (1 + shock_tech_τ)`。
    * `shock_tech_τ ~ TruncNormal(0, 0.03, -0.1, 0.1)`。

### 2.2 生产与库存

* 生产函数：
    $$output_τ = technology_τ \cdot capital\_stock_τ^{α} \cdot labor\_input_τ^{1-α}, \quad α = 0.33$$
* 库存动态：
    $$inventory_{τ+1} = inventory_τ + output_τ - goods\_sold_τ - spoilage_τ$$
    其中 `spoilage_τ = 0.01 * inventory_τ`。

### 2.3 定价与订单

* `goods_price_τ`
    * 决策规则：
        $$goods\_price_{τ+1} = clip(goods\_price_τ \cdot (1 + κ_p \cdot excess\_demand_τ),\ 0.5, 5.0)$$
        `κ_p = 0.4`，`excess_demand_τ = (aggregate_demand_τ - inventory_τ)/\max(inventory_τ, 1)`。

* `wage_offer_τ`
    * 公式：`wage_offer_τ = wage_base * (1 + 0.2 * labor_shortage_ratio_τ)`。
    * `labor_shortage_ratio_τ = clip((labor_demand_τ - employed_workers_τ)/max(labor_demand_τ, 1), -0.5, 0.5)`。

### 2.4 投资与融资

* 资本更新：
    $$capital\_stock_{τ+1} = (1 - depreciation\_rate^{tick}) \cdot capital\_stock_τ + investment_τ$$
* 投资决策：`investment_τ = clip(κ_i * (desired_capital_τ - capital_stock_τ), 0, cash_τ)`，`κ_i = 0.3`。
* 贷款需求：若 `cash_τ < payroll_requirement_τ + investment_τ`，申请 `loan_request_τ = payroll_requirement_τ + investment_τ - cash_τ`。
* 资金来源约束：企业仅通过自有现金或商业银行存贷款调节流动性，不发行债券或股票，也不参与政府债券投资。

### 2.5 约束

* 偿付能力 `cash_τ + inventory_τ * goods_price_τ + capital_stock_τ - debt_τ ≥ 0`。
* 工资义务：在每日第一个 tick 必须支付 `payroll_requirement_d = wage_offer_d * employed_workers_d`。

---

## 3. 商业银行代理 (Commercial Bank Agent)

### 3.1 状态变量

* `reserves_τ`：初始化 `TruncNormal(150, 30, 80, 220)`。
* `loans_τ`：初始化 `TruncNormal(300, 50, 180, 420)`。
* `deposits_τ`：初始化 `TruncNormal(360, 60, 220, 520)`。
* `equity_τ = reserves_τ + loans_τ - deposits_τ`。
* `non_performing_ratio_τ`：初始化 `Uniform(0.02, 0.05)`。

### 3.2 利率与报价

* 存款利率：
    $$deposit\_rate_τ^{annual} = clip(policy\_rate_τ + deposit\_rate\_spread\_base - 0.5 \cdot (capital\_adequacy\_target - capital\_adequacy_τ),\ -0.02, 0.1)$$

* 贷款利率：
    $$loan\_rate_τ^{annual} = clip(policy\_rate_τ + loan\_rate\_spread\_base + 0.5 \cdot \max(0, capital\_adequacy\_target - capital\_adequacy_τ),\ 0, 0.3)$$

其中 `capital_adequacy_τ = equity_τ / max(loans_τ, 1)`，目标比率 `capital_adequacy_target = 0.12`。

### 3.3 信贷供给

* 新贷款审批量：
    $$loan\_supply_τ = \max\left(0, \frac{reserves_τ - reserve\_requirement_τ}{1 + non\_performing\_ratio_τ}\right)$$
* 准备金要求：`reserve_requirement_τ = reserve_ratio_τ * deposits_τ`。
* 股东红利：若 `equity_τ > equity_target`，发放 `dividend_τ = 0.2 * (equity_τ - equity_target)`，其中 `equity_target = 0.08 * loans_τ`。

### 3.4 约束

* `actual_reserve_ratio_τ = reserves_τ / deposits_τ ≥ reserve_ratio_τ`。
* `capital_adequacy_τ ≥ 0.08`。若不满足，则暂停发放新贷款。

---

## 4. 央行代理 (Central Bank Agent)

### 4.1 状态变量

* `policy_rate_τ`：初始化 `policy_rate_base`。
* `reserve_ratio_τ`：初始化 `reserve_ratio_base`。
* `central_bank_assets_τ`：初始化 `TruncNormal(500, 80, 300, 700)`。
* `central_bank_liabilities_τ`：初始化 `central_bank_assets_τ`。

### 4.2 政策反应函数

* 泰勒规则：
    $$policy\_rate_{τ+1} = clip(policy\_rate\_base + \phi\_inflation \cdot (inflation\_rate_τ - target\_inflation) + \phi\_output \cdot output\_gap_τ,\ 0, 0.4)$$
    其中 `target_inflation = 0.02`。

* 准备金率调整：
    $$reserve\_ratio_{τ+1} = clip(reserve\_ratio_τ + 0.1 \cdot (credit\_growth_τ - credit\_target),\ 0.05, 0.2)$$
    `credit_growth_τ = (loans_τ - loans_{τ-1})/max(loans_{τ-1}, 1)`，`credit_target = 0.03`。

* 公开市场操作：若 `inflation_rate_τ > target_inflation + 0.02`，出售国债 `bond_sales_τ = 0.05 * central_bank_assets_τ`；若低于 `target_inflation - 0.02`，则购入同规模国债。

---

## 5. 政府代理 (Government Agent)

### 5.1 状态变量

* `gov_cash_τ`：初始化 `TruncNormal(150, 30, 80, 220)`。
* `gov_debt_τ`：初始化 `TruncNormal(200, 40, 100, 320)`。
* `tax_rate_income_τ`：初始化 `0.15`。
* `tax_rate_consumption_τ`：初始化 `0.05`。
* `government_spending_τ`：初始化 `20.0`。

### 5.2 财政规则

* 税率调节：
    $$tax\_rate\_income_{τ+1} = clip(tax\_rate\_income_τ + 0.1 \cdot (debt\_ratio_τ - debt\_target),\ 0.1, 0.35)$$
    `debt_ratio_τ = gov_debt_τ / max(gdp_τ, 1)`，`debt_target = 0.6`。

* 支出规则：
    $$government\_spending_{τ+1} = clip(\bar{G} + 0.4 \cdot unemployment\_gap_τ,\ 10, 40)$$
    `\bar{G} = 20`，`unemployment_gap_τ = unemployment_rate_τ - 0.05`。

* 转移支付：`transfer_payment_d = 5 * unemployed_households_d`，在每日第一个 tick 发放。

* 国债发行：若 `fiscal_balance_τ = tax_revenue_τ - government_spending_τ < 0`，发行 `bond_issue_τ = -fiscal_balance_τ`，利率 `bond_rate_τ = policy_rate_τ + 0.01`。

### 5.3 约束

* 预算：`gov_cash_{τ+1} = gov_cash_τ + tax_revenue_τ + bond_issue_τ - government_spending_τ - transfer_payment_τ`。
* 债务可持续性：`gov_debt_τ / gdp_τ ≤ 0.9`，若超过，强制压缩支出 `government_spending_{τ+1} = max(government_spending_τ - 5, 10)`。

---

## 6. 变量映射与对接

* 所有 `*_τ` 变量在 tick 结束后写入 `agent_state_history`。
* 家户、企业、银行的报价变量会在市场阶段作为订单提交，其他代理通过 `market_data` 获取宏观参数。
* 若新增变量，请同时在《世界设定》《市场设计》更新对应的初始化、范围与聚合方式。
