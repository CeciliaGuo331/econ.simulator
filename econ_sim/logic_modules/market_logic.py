"""封装仿真单步内市场出清与宏观指标更新的核心逻辑。

本模块负责在每个仿真 tick 中：
- 应用家庭层面的冲击（如资产变动/能力变动）；
- 央行政策应用；
- 劳动力市场撮合与失业率计算；
- 生产、商品市场清算；
- 工资与失业补助的支付；
- 储蓄/存款处理与利率更新；
- 税收征集与宏观指标（如 GDP、通胀指数）更新；

函数以不可变方式克隆工作状态，在内存中计算变更并最终生成
需要写回到持久化层的 StateUpdateCommand 列表与日志条目。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..data_access.models import (
    AgentKind,
    BankState,
    CentralBankState,
    EmploymentStatus,
    FirmState,
    GovernmentState,
    HouseholdShock,
    HouseholdState,
    MacroState,
    StateUpdateCommand,
    TickDecisions,
    TickLogEntry,
    WorldState,
    LedgerEntry,
)
from ..utils.settings import WorldConfig


@dataclass
class WorkingState:
    """运行期世界状态快照，用于在不修改原状态的前提下执行逻辑。"""

    households: Dict[int, HouseholdState]
    firm: FirmState
    government: GovernmentState
    bank: BankState
    central_bank: CentralBankState
    macro: MacroState


@dataclass
class TickEconomyMetrics:
    """记录单个仿真步内的经济指标，便于后续汇总与写回。"""

    goods_sold: float = 0.0
    consumption_value: float = 0.0
    wage_payments_firm: float = 0.0
    wage_payments_government: float = 0.0
    transfers: float = 0.0
    taxes: float = 0.0
    unemployment_rate: float = 1.0
    price_level: float = 0.0
    wage_level: float = 0.0


def execute_tick_logic(
    world_state: WorldState,
    decisions: TickDecisions,
    config: WorldConfig,
    shocks: Optional[Dict[int, HouseholdShock]] = None,
) -> Tuple[
    List[StateUpdateCommand], List[TickLogEntry], List["LedgerEntry"], Dict[str, Any]
]:
    """执行完整的市场流程，返回状态更新指令与日志列表。"""
    working = _clone_world_state(world_state)
    metrics = TickEconomyMetrics()
    logs: List[TickLogEntry] = []

    applied_shocks = shocks or {}
    if applied_shocks:
        asset_impacts: List[float] = []
        ability_impacts: List[float] = []
        for hid, shock in applied_shocks.items():
            household = working.households.get(hid)
            if household is None:
                continue
            household.balance_sheet.cash = max(
                0.0, household.balance_sheet.cash + shock.asset_delta
            )
            asset_impacts.append(shock.asset_delta)
            ability_impacts.append(shock.ability_multiplier)

        if asset_impacts or ability_impacts:
            logs.append(
                TickLogEntry(
                    tick=world_state.tick,
                    day=world_state.day,
                    message="household_shocks_applied",
                    context={
                        "households": len(applied_shocks),
                        "asset_delta_sum": float(sum(asset_impacts)),
                        "ability_mean": float(
                            np.mean(ability_impacts) if ability_impacts else 1.0
                        ),
                        "ability_std": float(
                            np.std(ability_impacts) if ability_impacts else 0.0
                        ),
                    },
                )
            )

    _apply_central_bank_policy(working, decisions)

    labor_log = _resolve_labor_market(
        working, decisions, config, metrics, world_state, applied_shocks
    )
    logs.append(labor_log)

    production_log = _run_production_phase(
        working, decisions, config, metrics, world_state
    )
    logs.append(production_log)

    finance_logs, finance_ledgers = _process_income_support(
        working, decisions, config, metrics, world_state
    )
    logs.extend(finance_logs)

    # collect ledgers produced during income support processing
    all_ledgers: List["LedgerEntry"] = []
    all_ledgers.extend(finance_ledgers)

    # process periodic coupon payments (if any)
    try:
        from ..new_logic import government_financial as _gov_fin

        c_updates, c_ledgers, c_log = _gov_fin.process_coupon_payments(
            working, tick=world_state.tick, day=world_state.day
        )

        # apply coupon updates to working snapshot
        def _apply_coupon_update(update: StateUpdateCommand) -> None:
            scope = update.scope
            changes = update.changes or {}
            if scope == AgentKind.BANK:
                if "balance_sheet" in changes:
                    bs = changes["balance_sheet"]
                    working.bank.balance_sheet.cash = float(
                        bs.get("cash", working.bank.balance_sheet.cash)
                    )
                if "bond_holdings" in changes:
                    working.bank.bond_holdings = changes["bond_holdings"]
            elif scope == AgentKind.HOUSEHOLD:
                hid = int(update.agent_id)
                hh = working.households[hid]
                if "balance_sheet" in changes:
                    bs = changes["balance_sheet"]
                    hh.balance_sheet.cash = float(bs.get("cash", hh.balance_sheet.cash))
                if "bond_holdings" in changes:
                    hh.bond_holdings = changes["bond_holdings"]
            elif scope == AgentKind.GOVERNMENT:
                if "balance_sheet" in changes:
                    bs = changes["balance_sheet"]
                    working.government.balance_sheet.cash = float(
                        bs.get("cash", working.government.balance_sheet.cash)
                    )
                if "debt_outstanding" in changes:
                    working.government.debt_outstanding = changes["debt_outstanding"]

        for up in c_updates:
            try:
                _apply_coupon_update(up)
            except Exception:
                pass

        all_ledgers.extend(c_ledgers)
        logs.append(c_log)
    except Exception:
        # swallow to avoid breaking tick flow
        pass

    goods_log = _clear_goods_market(working, decisions, config, metrics, world_state)
    logs.append(goods_log)

    savings_log = _process_savings(working, decisions, config, metrics, world_state)
    logs.append(savings_log)

    tax_log = _collect_taxes(working, decisions, config, metrics, world_state)
    logs.append(tax_log)

    macro_update = _update_macro_metrics(working, metrics, world_state)

    updates = _build_state_updates(world_state, working, macro_update)

    # expose market signals (e.g., bond_yield) from working macro
    market_signals: Dict[str, Any] = {}
    if getattr(working.macro, "bond_yield", None) is not None:
        market_signals["bond_yield"] = float(working.macro.bond_yield)

    return updates, logs, all_ledgers, market_signals


def _clone_world_state(world_state: WorldState) -> WorkingState:
    """深拷贝当前世界状态，避免直接修改持久化对象。"""
    return WorkingState(
        households={
            hid: household.model_copy(deep=True)
            for hid, household in world_state.households.items()
        },
        firm=world_state.firm.model_copy(deep=True),
        government=world_state.government.model_copy(deep=True),
        bank=world_state.bank.model_copy(deep=True),
        central_bank=world_state.central_bank.model_copy(deep=True),
        macro=world_state.macro.model_copy(deep=True),
    )


def _apply_central_bank_policy(working: WorkingState, decisions: TickDecisions) -> None:
    """根据央行决策更新工作状态中的利率与准备金率。"""
    working.central_bank.base_rate = decisions.central_bank.policy_rate
    working.central_bank.reserve_ratio = decisions.central_bank.reserve_ratio
    # execute any OMO operations submitted in the central bank decision
    from ..new_logic import central_bank_policy as _cb_policy

    # helper to apply assign updates to working snapshot
    def _apply_update(update: StateUpdateCommand) -> None:
        scope = update.scope
        aid = update.agent_id
        changes = update.changes or {}
        if scope == AgentKind.BANK:
            if "balance_sheet" in changes:
                bs = changes["balance_sheet"]
                working.bank.balance_sheet.cash = float(
                    bs.get("cash", working.bank.balance_sheet.cash)
                )
                working.bank.balance_sheet.deposits = float(
                    bs.get("deposits", working.bank.balance_sheet.deposits)
                )
            if "bond_holdings" in changes:
                working.bank.bond_holdings = changes["bond_holdings"]
        elif scope == AgentKind.CENTRAL_BANK:
            if "balance_sheet" in changes:
                bs = changes["balance_sheet"]
                working.central_bank.balance_sheet.cash = float(
                    bs.get("cash", working.central_bank.balance_sheet.cash)
                )
            if "bond_holdings" in changes:
                working.central_bank.bond_holdings = changes["bond_holdings"]

    for op in getattr(decisions.central_bank, "omo_ops", []):
        try:
            res = _cb_policy.open_market_operation(
                working,
                bond_id=op.get("bond_id"),
                quantity=op.get("quantity", 0.0),
                side=op.get("side"),
                price=op.get("price", 0.0),
                tick=0,
                day=0,
            )
            for up in res.get("updates", []):
                _apply_update(up)
        except Exception:
            # swallow to avoid breaking tick flow; errors should be logged in future
            pass


def _resolve_labor_market(
    working: WorkingState,
    decisions: TickDecisions,
    config: WorldConfig,
    metrics: TickEconomyMetrics,
    world_state: WorldState,
    shocks: Dict[int, HouseholdShock],
) -> TickLogEntry:
    """撮合劳动力市场的供需并计算失业率，记录相关日志。"""
    firm = working.firm
    government = working.government

    unemployed_candidates = []
    for hid, decision in decisions.households.items():
        household = working.households[hid]
        if (
            household.employment_status is EmploymentStatus.UNEMPLOYED
            and decision.labor_supply > 0.5
        ):
            unemployed_candidates.append(household)

    def _effective_skill(h: HouseholdState) -> float:
        shock = shocks.get(h.id)
        multiplier = shock.ability_multiplier if shock else 1.0
        return h.skill * max(0.1, multiplier)

    unemployed_candidates.sort(key=_effective_skill, reverse=True)

    desired_firm_workers = max(0, len(firm.employees) + decisions.firm.hiring_demand)
    firm_employees = set(firm.employees)
    for candidate in unemployed_candidates:
        if len(firm_employees) >= desired_firm_workers:
            break
        firm_employees.add(candidate.id)
        candidate.employment_status = EmploymentStatus.EMPLOYED_FIRM
        candidate.employer_id = firm.id
        candidate.wage_income = decisions.firm.wage_offer

    desired_government_jobs = max(
        decisions.government.government_jobs, len(government.employees)
    )
    government_employees = set(government.employees)
    for candidate in unemployed_candidates:
        if candidate.id in firm_employees:
            continue
        if len(government_employees) >= desired_government_jobs:
            break
        government_employees.add(candidate.id)
        candidate.employment_status = EmploymentStatus.EMPLOYED_GOVERNMENT
        candidate.employer_id = government.id
        candidate.wage_income = decisions.firm.wage_offer * 0.8

    firm.employees = sorted(firm_employees)
    government.employees = sorted(government_employees)

    total_employed = len(firm.employees) + len(government.employees)
    metrics.unemployment_rate = float(
        np.clip(1.0 - total_employed / max(1.0, len(working.households)), 0.0, 1.0)
    )

    return TickLogEntry(
        tick=world_state.tick,
        day=world_state.day,
        message="labor_market_cleared",
        context={
            "firm_headcount": len(firm.employees),
            "government_headcount": len(government.employees),
            "unemployment_rate": metrics.unemployment_rate,
        },
    )


def _run_production_phase(
    working: WorkingState,
    decisions: TickDecisions,
    config: WorldConfig,
    metrics: TickEconomyMetrics,
    world_state: WorldState,
) -> TickLogEntry:
    """根据企业决策推进生产阶段，同时更新价格与工资水平。"""
    firm = working.firm
    capacity = max(1, len(firm.employees)) * max(firm.productivity, 0.1)
    produced_goods = float(np.clip(decisions.firm.planned_production, 0.0, capacity))
    firm.balance_sheet.inventory_goods = max(
        0.0, firm.balance_sheet.inventory_goods + produced_goods
    )
    firm.price = decisions.firm.price
    firm.wage_offer = decisions.firm.wage_offer
    metrics.price_level = firm.price
    metrics.wage_level = firm.wage_offer

    return TickLogEntry(
        tick=world_state.tick,
        day=world_state.day,
        message="production_phase_completed",
        context={
            "produced_goods": produced_goods,
            "inventory": firm.balance_sheet.inventory_goods,
        },
    )


def _process_income_support(
    working: WorkingState,
    decisions: TickDecisions,
    config: WorldConfig,
    metrics: TickEconomyMetrics,
    world_state: WorldState,
) -> Tuple[List[TickLogEntry], List[LedgerEntry]]:
    """处理工资发放与失业补助，反映至家庭与财政账户。"""
    firm = working.firm
    government = working.government
    logs: List[TickLogEntry] = []

    firm_payroll = 0.0
    for hid in firm.employees:
        household = working.households[hid]
        firm_payroll += decisions.firm.wage_offer
        household.balance_sheet.cash += decisions.firm.wage_offer

    metrics.wage_payments_firm = firm_payroll
    firm.balance_sheet.cash = max(0.0, firm.balance_sheet.cash - firm_payroll)

    gov_payroll = 0.0
    for hid in government.employees:
        household = working.households[hid]
        wage = decisions.firm.wage_offer * 0.8
        gov_payroll += wage
        household.balance_sheet.cash += wage

    metrics.wage_payments_government = gov_payroll
    government.balance_sheet.cash = max(
        0.0, government.balance_sheet.cash - gov_payroll
    )

    # process unemployment benefits and means-tested transfers via new logic
    # import locally to avoid circular import at module load time
    from ..new_logic.government_transfers import (
        unemployment_benefit,
        means_tested_transfer,
    )

    # these functions return StateUpdateCommand lists and ledger entries; we apply updates to working
    # pass any bond bids submitted in decisions into transfer functions so they can perform marketized issuance
    u_updates, u_ledger, u_log = unemployment_benefit(
        world_state, decisions.government, bids=getattr(decisions, "bond_bids", None)
    )
    m_updates, m_ledger, m_log = means_tested_transfer(
        world_state, decisions.government, bids=getattr(decisions, "bond_bids", None)
    )

    # helper to apply assign updates to working snapshot (supports balance_sheet and simple fields)
    def _apply_update(update: StateUpdateCommand) -> None:
        scope = update.scope
        aid = update.agent_id
        changes = update.changes or {}
        if scope == AgentKind.HOUSEHOLD:
            hid = int(aid)
            hh = working.households[hid]
            if "balance_sheet" in changes:
                bs = changes["balance_sheet"]
                # update fields present in balance_sheet dict
                hh.balance_sheet.cash = float(bs.get("cash", hh.balance_sheet.cash))
                hh.balance_sheet.deposits = float(
                    bs.get("deposits", hh.balance_sheet.deposits)
                )
                hh.balance_sheet.loans = float(bs.get("loans", hh.balance_sheet.loans))
                hh.balance_sheet.inventory_goods = float(
                    bs.get("inventory_goods", hh.balance_sheet.inventory_goods)
                )
            if "employment_status" in changes:
                from ..data_access.models import EmploymentStatus as _ES

                hh.employment_status = _ES(changes["employment_status"])
            if "wage_income" in changes:
                hh.wage_income = float(changes["wage_income"])
            if "bond_holdings" in changes:
                hh.bond_holdings = changes["bond_holdings"]
        elif scope == AgentKind.GOVERNMENT:
            gov = working.government
            if "balance_sheet" in changes:
                bs = changes["balance_sheet"]
                gov.balance_sheet.cash = float(bs.get("cash", gov.balance_sheet.cash))
                gov.balance_sheet.deposits = float(
                    bs.get("deposits", gov.balance_sheet.deposits)
                )
                gov.balance_sheet.loans = float(
                    bs.get("loans", gov.balance_sheet.loans)
                )
                gov.balance_sheet.inventory_goods = float(
                    bs.get("inventory_goods", gov.balance_sheet.inventory_goods)
                )
            if "tax_rate" in changes:
                gov.tax_rate = float(changes["tax_rate"])
            if "debt_outstanding" in changes:
                gov.debt_outstanding = changes["debt_outstanding"]
            if "debt_instruments" in changes:
                gov.debt_instruments = changes["debt_instruments"]

    # apply all updates returned by transfer functions
    all_ledgers = []
    for up in u_updates + m_updates:
        _apply_update(up)

    # collect ledgers from transfer functions (if they returned them)
    try:
        all_ledgers.extend(u_ledger)
    except Exception:
        pass
    try:
        all_ledgers.extend(m_ledger)
    except Exception:
        pass

    # record a ledger summary log (serialize a small slice for safety)
    import json

    ledger_preview = []
    for entry in all_ledgers[:10]:
        try:
            ledger_preview.append(
                {
                    "account": (
                        entry.account_kind.value
                        if hasattr(entry.account_kind, "value")
                        else str(entry.account_kind)
                    ),
                    "entity": entry.entity_id,
                    "type": entry.entry_type,
                    "amount": float(entry.amount),
                    "ref": entry.reference,
                }
            )
        except Exception:
            continue

    logs.append(
        TickLogEntry(
            tick=world_state.tick,
            day=world_state.day,
            message="ledgers_recorded",
            context={
                "ledger_count": len(all_ledgers),
                "ledger_preview": json.dumps(ledger_preview),
            },
        )
    )

    metrics.transfers = (
        metrics.transfers + 0.0
    )  # keep existing metric usage; detailed amounts may be in logs

    # append logs from the transfer functions
    logs.append(
        TickLogEntry(
            tick=world_state.tick,
            day=world_state.day,
            message="wages_disbursed",
            context={
                "firm_payroll": firm_payroll,
                "government_payroll": gov_payroll,
                "benefits": "see_transfer_logs",
            },
        )
    )
    logs.append(u_log)
    logs.append(m_log)

    return logs, all_ledgers


def _clear_goods_market(
    working: WorkingState,
    decisions: TickDecisions,
    config: WorldConfig,
    metrics: TickEconomyMetrics,
    world_state: WorldState,
) -> TickLogEntry:
    """按照家庭消费决策与企业库存清算商品市场。"""
    firm = working.firm
    price = max(0.01, firm.price)

    total_goods_demand = 0.0
    planned_goods: Dict[int, float] = {}

    for hid, decision in decisions.households.items():
        household = working.households[hid]
        affordable = household.balance_sheet.cash / price
        planned = min(decision.consumption_budget / price, affordable)
        planned = float(np.clip(planned, 0.0, 200.0))
        planned_goods[hid] = planned
        total_goods_demand += planned

    available_goods = firm.balance_sheet.inventory_goods
    allocation_ratio = (
        1.0
        if total_goods_demand <= available_goods
        else available_goods / max(total_goods_demand, 1e-6)
    )

    goods_sold = 0.0
    consumption_value = 0.0
    for hid, planned in planned_goods.items():
        take_goods = planned * allocation_ratio
        payment = take_goods * price
        household = working.households[hid]
        household.balance_sheet.cash = max(0.0, household.balance_sheet.cash - payment)
        household.last_consumption = take_goods
        goods_sold += take_goods
        consumption_value += payment

    firm.balance_sheet.inventory_goods = max(
        0.0, firm.balance_sheet.inventory_goods - goods_sold
    )
    firm.balance_sheet.cash += consumption_value
    firm.last_sales = goods_sold
    metrics.goods_sold = goods_sold
    metrics.consumption_value = consumption_value

    return TickLogEntry(
        tick=world_state.tick,
        day=world_state.day,
        message="goods_market_cleared",
        context={"goods_sold": goods_sold, "consumption_value": consumption_value},
    )


def _process_savings(
    working: WorkingState,
    decisions: TickDecisions,
    config: WorldConfig,
    metrics: TickEconomyMetrics,
    world_state: WorldState,
) -> TickLogEntry:
    """将家庭现金按储蓄率转存为存款，并更新金融市场利率。"""
    bank = working.bank
    total_new_deposits = 0.0

    for hid, decision in decisions.households.items():
        household = working.households[hid]
        savings = household.balance_sheet.cash * decision.savings_rate
        if savings <= 0:
            continue
        household.balance_sheet.cash -= savings
        household.balance_sheet.deposits += savings
        total_new_deposits += savings

    bank.balance_sheet.deposits += total_new_deposits
    bank.balance_sheet.cash += total_new_deposits
    bank.deposit_rate = decisions.bank.deposit_rate
    bank.loan_rate = decisions.bank.loan_rate

    return TickLogEntry(
        tick=world_state.tick,
        day=world_state.day,
        message="savings_processed",
        context={"new_deposits": total_new_deposits},
    )


def _collect_taxes(
    working: WorkingState,
    decisions: TickDecisions,
    config: WorldConfig,
    metrics: TickEconomyMetrics,
    world_state: WorldState,
) -> TickLogEntry:
    """依据家庭工资收入征收税收，并累计到政府账户。"""
    tax_rate = decisions.government.tax_rate
    government = working.government
    total_tax = 0.0

    for household in working.households.values():
        taxable_income = max(0.0, household.wage_income)
        tax = taxable_income * tax_rate
        if tax <= 0:
            continue
        deduction = min(tax, household.balance_sheet.cash)
        household.balance_sheet.cash -= deduction
        total_tax += deduction

    metrics.taxes = total_tax
    government.tax_rate = tax_rate
    government.balance_sheet.cash += total_tax

    return TickLogEntry(
        tick=world_state.tick,
        day=world_state.day,
        message="taxes_collected",
        context={"tax_collected": total_tax},
    )


def _update_macro_metrics(
    working: WorkingState,
    metrics: TickEconomyMetrics,
    world_state: WorldState,
) -> StateUpdateCommand:
    """组合并平滑宏观指标，生成对应的状态更新命令。"""
    previous_price = working.macro.price_index or metrics.price_level or 100.0
    price_level = metrics.price_level or previous_price
    price_index = 0.9 * previous_price + 0.1 * price_level

    previous_wage = working.macro.wage_index or metrics.wage_level or 100.0
    wage_level = metrics.wage_level or previous_wage
    wage_index = 0.9 * previous_wage + 0.1 * wage_level

    inflation = 0.0
    if previous_price:
        inflation = (price_index - previous_price) / previous_price

    gdp = (
        metrics.consumption_value
        + metrics.wage_payments_government
        + metrics.wage_payments_firm
        + metrics.transfers
    )

    working.macro.gdp = gdp
    working.macro.inflation = inflation
    working.macro.unemployment_rate = metrics.unemployment_rate
    working.macro.price_index = price_index
    working.macro.wage_index = wage_index

    return StateUpdateCommand.assign(
        AgentKind.MACRO,
        agent_id=None,
        gdp=gdp,
        inflation=inflation,
        unemployment_rate=metrics.unemployment_rate,
        price_index=price_index,
        wage_index=wage_index,
    )


def _build_state_updates(
    original: WorldState,
    working: WorkingState,
    macro_update: StateUpdateCommand,
) -> List[StateUpdateCommand]:
    """比较运行期与原始状态，生成需要写回的数据更新列表。"""
    updates: List[StateUpdateCommand] = [macro_update]

    for hid, household in working.households.items():
        original_household = original.households[hid]
        if household.model_dump() != original_household.model_dump():
            updates.append(
                StateUpdateCommand.assign(
                    AgentKind.HOUSEHOLD,
                    agent_id=hid,
                    balance_sheet=household.balance_sheet.model_dump(),
                    employment_status=household.employment_status.value,
                    employer_id=household.employer_id,
                    wage_income=household.wage_income,
                    last_consumption=household.last_consumption,
                )
            )

    if working.firm.model_dump() != original.firm.model_dump():
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.FIRM,
                agent_id=working.firm.id,
                balance_sheet=working.firm.balance_sheet.model_dump(),
                price=working.firm.price,
                wage_offer=working.firm.wage_offer,
                employees=working.firm.employees,
                last_sales=working.firm.last_sales,
            )
        )

    if working.government.model_dump() != original.government.model_dump():
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.GOVERNMENT,
                agent_id=working.government.id,
                balance_sheet=working.government.balance_sheet.model_dump(),
                tax_rate=working.government.tax_rate,
                employees=working.government.employees,
            )
        )

    if working.bank.model_dump() != original.bank.model_dump():
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.BANK,
                agent_id=working.bank.id,
                balance_sheet=working.bank.balance_sheet.model_dump(),
                deposit_rate=working.bank.deposit_rate,
                loan_rate=working.bank.loan_rate,
            )
        )

    if working.central_bank.model_dump() != original.central_bank.model_dump():
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.CENTRAL_BANK,
                agent_id=working.central_bank.id,
                base_rate=working.central_bank.base_rate,
                reserve_ratio=working.central_bank.reserve_ratio,
            )
        )

    return updates
