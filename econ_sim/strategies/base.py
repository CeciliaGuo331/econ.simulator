"""为不同主体提供基准启发式策略，便于基础仿真与冒烟测试。"""

from __future__ import annotations

from typing import Dict

import numpy as np

from ..data_access.models import (
    BankDecision,
    BankState,
    CentralBankDecision,
    CentralBankState,
    FirmDecision,
    FirmState,
    GovernmentDecision,
    GovernmentState,
    HouseholdDecision,
    HouseholdState,
    PublicMarketData,
    WorldState,
)
from ..utils.settings import WorldConfig


class BaseHouseholdStrategy:
    """家庭主体的基准消费-储蓄策略。"""

    def __init__(self, config: WorldConfig) -> None:
        """保存全局配置，以便在决策时引用市场参数。"""
        self.config = config

    def decide(
        self, household: HouseholdState, market: PublicMarketData
    ) -> HouseholdDecision:
        """根据家庭特征与市场价格确定消费预算、储蓄率与劳供。"""
        available_income = household.balance_sheet.cash + household.wage_income
        subsistence_cost = (
            self.config.markets.goods.subsistence_consumption * market.goods_price
        )
        discretionary_budget = max(0.0, available_income - subsistence_cost)
        preference_weight = np.clip(household.preference, 0.1, 0.9)
        planned_consumption = (
            subsistence_cost + discretionary_budget * preference_weight * 0.6
        )
        planned_consumption = float(min(planned_consumption, available_income))

        # Target savings rate increases when already employed and cash is low.
        savings_rate = 0.2 + (0.1 if available_income > subsistence_cost * 1.5 else 0.0)
        savings_rate = float(np.clip(savings_rate, 0.0, 0.8))

        labor_supply = (
            1.0 if household.employment_status.name.startswith("UNEMP") else 0.8
        )

        # Education decision: only evaluated on daily decision ticks
        is_studying = False
        education_payment = 0.0
        try:
            is_daily = bool(getattr(market, "is_daily_decision_tick", False))
        except Exception:
            is_daily = False

        if is_daily:
            # do not study if currently employed
            if not household.employment_status.name.startswith("EMP"):
                try:
                    cost = float(self.config.policies.education_cost_per_day)
                    gain = float(self.config.policies.education_gain)
                except Exception:
                    cost = 2.0
                    gain = 0.05

                assets = float(
                    (household.balance_sheet.cash or 0.0)
                    + (household.balance_sheet.deposits or 0.0)
                )
                expected_wage_gain = float(market.wage_offer or 0.0) * (0.6 * gain)
                if assets > cost * 20 and expected_wage_gain > cost:
                    is_studying = True
                    education_payment = cost

        return HouseholdDecision(
            labor_supply=labor_supply,
            consumption_budget=planned_consumption,
            savings_rate=savings_rate,
            is_studying=is_studying,
            education_payment=education_payment,
        )


class BaseFirmStrategy:
    """企业主体的基准生产与招聘策略。"""

    def __init__(self, config: WorldConfig) -> None:
        """记录配置项，支持根据市场参数调节生产计划。"""
        self.config = config

    def decide(self, firm: FirmState, world: WorldState) -> FirmDecision:
        """依据库存、销量与生产率计算价格、产量与招聘需求。"""
        target_inventory = (
            self.config.simulation.num_households
            * self.config.markets.goods.subsistence_consumption
        )
        inventory_gap = target_inventory - firm.balance_sheet.inventory_goods
        expected_demand = max(target_inventory, firm.last_sales * 1.1)
        planned_production = max(expected_demand + inventory_gap, 0.0)

        effective_productivity = max(firm.productivity, 0.1)
        desired_workers = int(np.ceil(planned_production / effective_productivity))
        hiring_demand = max(0, desired_workers - len(firm.employees))

        price_adjustment = 1.0
        if firm.balance_sheet.inventory_goods < target_inventory * 0.8:
            price_adjustment = 1.05
        elif firm.balance_sheet.inventory_goods > target_inventory * 1.2:
            price_adjustment = 0.97

        new_price = float(
            np.clip(firm.price * price_adjustment, 0.5 * firm.price, 2.0 * firm.price)
        )
        wage_offer = float(
            np.clip(
                self.config.markets.labor.base_wage * (1 + hiring_demand * 0.01),
                50.0,
                200.0,
            )
        )

        return FirmDecision(
            price=new_price,
            planned_production=float(planned_production),
            wage_offer=wage_offer,
            hiring_demand=hiring_demand,
        )


class BaseGovernmentStrategy:
    """政府主体的基准财政政策与就业平滑策略。"""

    def __init__(self, config: WorldConfig) -> None:
        """缓存配置，用于读取政策目标值。"""
        self.config = config

    def decide(
        self, government: GovernmentState, macro_unemployment: float
    ) -> GovernmentDecision:
        """结合宏观失业率调整税率、公共就业岗位与补贴预算。"""
        target_tax = self.config.policies.tax_rate
        tax_rate = float(
            np.clip(0.5 * government.tax_rate + 0.5 * target_tax, 0.05, 0.6)
        )

        unemployment_gap = max(0.0, macro_unemployment - 0.07)
        additional_jobs = int(
            round(unemployment_gap * self.config.simulation.num_households * 0.2)
        )
        government_jobs = max(
            self.config.markets.labor.government_jobs,
            len(government.employees) + additional_jobs,
        )

        transfer_budget = float(
            self.config.policies.unemployment_benefit
            * self.config.simulation.num_households
            * unemployment_gap
        )

        return GovernmentDecision(
            tax_rate=tax_rate,
            government_jobs=government_jobs,
            transfer_budget=transfer_budget,
        )


class BaseBankStrategy:
    """银行主体的基准利率与信贷供给策略。"""

    def __init__(self, config: WorldConfig) -> None:
        """存储配置以便计算合规准备金与基准利率。"""
        self.config = config

    def decide(self, bank: BankState, central_bank: CentralBankState) -> BankDecision:
        """根据央行政策与银行资产负债表调整存贷利率与放贷额度。"""
        policy_rate = central_bank.base_rate
        spread = 0.03
        loan_rate = float(np.clip(policy_rate + spread, 0.02, 0.25))
        deposit_rate = float(np.clip(policy_rate * 0.6, 0.0, loan_rate - 0.005))

        loanable_funds = max(
            0.0,
            bank.balance_sheet.deposits * (1 - central_bank.reserve_ratio)
            - bank.balance_sheet.loans,
        )
        return BankDecision(
            deposit_rate=deposit_rate,
            loan_rate=loan_rate,
            loan_supply=float(loanable_funds),
        )


class BaseCentralBankStrategy:
    """央行主体的基准货币政策规则。"""

    def __init__(self, config: WorldConfig) -> None:
        """保存配置以读取目标通胀与失业率。"""
        self.config = config

    def decide(
        self, central_bank: CentralBankState, macro: PublicMarketData
    ) -> CentralBankDecision:
        """使用类泰勒规则计算政策利率与法定准备金率。"""
        inflation_gap = macro.inflation - central_bank.inflation_target
        unemployment_gap = macro.unemployment_rate - central_bank.unemployment_target

        policy_rate = (
            central_bank.base_rate + 0.5 * inflation_gap - 0.3 * unemployment_gap
        )
        policy_rate = float(np.clip(policy_rate, 0.0, 0.25))

        reserve_ratio = float(
            np.clip(central_bank.reserve_ratio + 0.1 * unemployment_gap, 0.05, 0.3)
        )

        return CentralBankDecision(policy_rate=policy_rate, reserve_ratio=reserve_ratio)


class StrategyBundle:
    """聚合所有主体的基准策略，便于在调度器中统一访问。"""

    def __init__(self, config: WorldConfig, world_state: WorldState) -> None:
        """为每个主体实例化对应策略对象并缓存。"""
        self.households: Dict[int, BaseHouseholdStrategy] = {
            hid: BaseHouseholdStrategy(config) for hid in world_state.households
        }
        if (
            world_state.firm is None
            or world_state.bank is None
            or world_state.government is None
            or world_state.central_bank is None
        ):
            raise ValueError("缺少核心主体状态，无法构建策略集合")
        self.firm = BaseFirmStrategy(config)
        self.government = BaseGovernmentStrategy(config)
        self.bank = BaseBankStrategy(config)
        self.central_bank = BaseCentralBankStrategy(config)

    def household_strategy(self, household_id: int) -> BaseHouseholdStrategy:
        """返回指定家庭的策略对象，供决策模块调用。"""
        return self.households[household_id]
