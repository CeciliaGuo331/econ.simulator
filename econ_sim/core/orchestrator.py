"""最小的 orchestrator wrapper，用于在开发期间调用 `econ_sim.logic_modules` 中的经济逻辑。

该文件提供一个干净的 `run_tick_new(world_state)` 入口，生成（或回退生成）基线决策，
并调用 `logic_modules.market_logic.execute_tick_logic`。返回完整的四元组：
updates, logs, ledgers, market_signals。

设计原则：不修改 `econ_sim/logic_modules`，在缺失 baseline 生成器时使用保守回退。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


# Lightweight compatibility stubs for API layer imports.
# The project now uses a modular orchestrator (run_tick_new). To avoid
# breaking the API import graph (some modules still import these names),
# provide minimal exception and class stubs. These are intentionally
# lightweight; full orchestrator implementations live elsewhere (orchestrator_factory).
class MissingAgentScriptsError(Exception):
    """Raised when requested simulation cannot run because required agent scripts are missing."""


class SimulationNotFoundError(Exception):
    """Raised when a simulation id is not found."""


class SimulationStateError(Exception):
    """Raised on invalid state transitions; may carry a .tick attribute."""


class SimulationOrchestrator:
    """Minimal placeholder type used by API imports. Real orchestrators are provided by orchestrator_factory."""

    pass


from ..data_access.models import (
    StateUpdateCommand,
    TickLogEntry,
    WorldState,
    TickDecisions,
    HouseholdDecision,
    FirmDecision,
    BankDecision,
    GovernmentDecision,
    CentralBankDecision,
)
from ..utils.settings import get_world_config


def _fallback_baseline(world_state: WorldState) -> TickDecisions:
    """Construct a conservative default TickDecisions when no baseline generator exists.

    The goal is to produce sensible defaults so market submodules can run during
    early development / tests even if a full baseline generator isn't present.
    """
    households: Dict[int, HouseholdDecision] = {}
    for hid, hh in world_state.households.items():
        households[hid] = HouseholdDecision(
            labor_supply=getattr(hh, "labor_supply", 1.0),
            consumption_budget=float(getattr(hh.balance_sheet, "cash", 0.0)),
            savings_rate=0.1,
        )

    firm = world_state.firm
    if firm is None:
        firm_decision = FirmDecision(
            price=10.0, planned_production=0.0, wage_offer=80.0, hiring_demand=0
        )
    else:
        firm_decision = FirmDecision(
            price=getattr(firm, "price", 10.0),
            planned_production=getattr(firm, "planned_production", 0.0),
            wage_offer=getattr(firm, "wage_offer", 80.0),
            hiring_demand=0,
        )

    bank = world_state.bank
    if bank is None:
        bank_decision = BankDecision(deposit_rate=0.01, loan_rate=0.05, loan_supply=0.0)
    else:
        bank_decision = BankDecision(
            deposit_rate=getattr(bank, "deposit_rate", 0.01),
            loan_rate=getattr(bank, "loan_rate", 0.05),
            loan_supply=0.0,
        )

    government = world_state.government
    if government is None:
        government_decision = GovernmentDecision(
            tax_rate=0.15, government_jobs=0, transfer_budget=0.0
        )
    else:
        government_decision = GovernmentDecision(
            tax_rate=getattr(government, "tax_rate", 0.15),
            government_jobs=len(getattr(government, "employees", [])),
            transfer_budget=getattr(government, "spending", 0.0),
        )

    central_bank = world_state.central_bank
    if central_bank is None:
        central_decision = CentralBankDecision(policy_rate=0.03, reserve_ratio=0.1)
    else:
        central_decision = CentralBankDecision(
            policy_rate=getattr(central_bank, "base_rate", 0.03),
            reserve_ratio=getattr(central_bank, "reserve_ratio", 0.1),
        )

    return TickDecisions(
        households=households,
        firm=firm_decision,
        bank=bank_decision,
        government=government_decision,
        central_bank=central_decision,
    )


def run_tick_new(
    world_state: WorldState,
) -> Tuple[List[StateUpdateCommand], List[TickLogEntry], List[Any], Dict[str, Any]]:
    """Run a single tick using the modular market subsystems in `econ_sim.logic_modules`.

    The function attempts to use a baseline generator under `logic_modules.baseline_stub` if
    available; otherwise it falls back to `_fallback_baseline`. It then invokes submodules in
    a sensible order and aggregates updates/logs/ledgers/market_signals.
    """
    config = get_world_config()

    # baseline decisions
    try:
        from ..logic_modules import baseline_stub  # type: ignore

        decisions = baseline_stub.generate_baseline_decisions(world_state)
    except Exception:
        decisions = _fallback_baseline(world_state)

    # collectors
    updates: List[StateUpdateCommand] = []
    logs: List[TickLogEntry] = []
    ledgers: List[Any] = []
    market_signals: Dict[str, Any] = {}

    tick = world_state.tick
    day = world_state.day

    # 1) coupon payments
    try:
        from ..logic_modules import government_financial

        c_updates, c_ledgers, c_log = government_financial.process_coupon_payments(
            world_state, tick=tick, day=day
        )
        updates.extend(c_updates)
        ledgers.extend(c_ledgers)
        logs.append(c_log)
    except Exception:
        pass

    # 2) labor market
    try:
        from ..logic_modules import labor_market

        l_updates, l_log = labor_market.resolve_labor_market_new(world_state, decisions)
        updates.extend(l_updates)
        logs.append(l_log)
    except Exception:
        pass

    # 3) wages (simple settlement)
    try:
        firm = getattr(world_state, "firm", None)
        government = getattr(world_state, "government", None)
        wage_updates: List[StateUpdateCommand] = []
        firm_payroll = 0.0
        gov_payroll = 0.0

        if firm is not None:
            for hid in getattr(firm, "employees", []):
                try:
                    hh = world_state.households[hid]
                    wage = float(decisions.firm.wage_offer)
                    hh.balance_sheet.cash = float(hh.balance_sheet.cash) + wage
                    firm_payroll += wage
                    wage_updates.append(
                        StateUpdateCommand.assign(
                            scope=__import__(
                                "econ_sim.data_access.models", fromlist=["AgentKind"]
                            ).AgentKind.HOUSEHOLD,
                            agent_id=hid,
                            balance_sheet=hh.balance_sheet.model_dump(),
                            wage_income=wage,
                        )
                    )
                except Exception:
                    continue
            if firm_payroll > 0:
                try:
                    firm.balance_sheet.cash = (
                        float(firm.balance_sheet.cash) - firm_payroll
                    )
                except Exception:
                    pass
                wage_updates.append(
                    StateUpdateCommand.assign(
                        scope=__import__(
                            "econ_sim.data_access.models", fromlist=["AgentKind"]
                        ).AgentKind.FIRM,
                        agent_id=firm.id,
                        balance_sheet=firm.balance_sheet.model_dump(),
                    )
                )

        if government is not None:
            for hid in getattr(government, "employees", []):
                try:
                    hh = world_state.households[hid]
                    wage = float(decisions.firm.wage_offer * 0.8)
                    hh.balance_sheet.cash = float(hh.balance_sheet.cash) + wage
                    gov_payroll += wage
                    wage_updates.append(
                        StateUpdateCommand.assign(
                            scope=__import__(
                                "econ_sim.data_access.models", fromlist=["AgentKind"]
                            ).AgentKind.HOUSEHOLD,
                            agent_id=hid,
                            balance_sheet=hh.balance_sheet.model_dump(),
                            wage_income=wage,
                        )
                    )
                except Exception:
                    continue
            if gov_payroll > 0:
                try:
                    government.balance_sheet.cash = (
                        float(government.balance_sheet.cash) - gov_payroll
                    )
                except Exception:
                    pass
                wage_updates.append(
                    StateUpdateCommand.assign(
                        scope=__import__(
                            "econ_sim.data_access.models", fromlist=["AgentKind"]
                        ).AgentKind.GOVERNMENT,
                        agent_id=government.id,
                        balance_sheet=government.balance_sheet.model_dump(),
                    )
                )

        if wage_updates:
            updates.extend(wage_updates)
            from ..data_access.models import TickLogEntry as _TLE

            logs.append(
                _TLE(
                    tick=tick,
                    day=day,
                    message="wages_disbursed",
                    context={
                        "firm_payroll": float(firm_payroll),
                        "government_payroll": float(gov_payroll),
                    },
                )
            )
    except Exception:
        pass

    # 4) goods market
    try:
        from ..logic_modules import goods_market

        g_updates, g_log = goods_market.clear_goods_market_new(world_state, decisions)
        updates.extend(g_updates)
        logs.append(g_log)
    except Exception:
        pass

    # 5) government transfers
    try:
        from ..logic_modules import government_transfers

        u_updates, u_ledgers, u_log = government_transfers.unemployment_benefit(
            world_state,
            decisions.government,
            bids=getattr(decisions, "bond_bids", None),
        )
        m_updates, m_ledgers, m_log = government_transfers.means_tested_transfer(
            world_state,
            decisions.government,
            bids=getattr(decisions, "bond_bids", None),
        )
        updates.extend(u_updates)
        updates.extend(m_updates)
        ledgers.extend(u_ledgers)
        ledgers.extend(m_ledgers)
        logs.append(u_log)
        logs.append(m_log)
    except Exception:
        pass

    # 6) central bank OMO
    try:
        from ..logic_modules import central_bank

        omo_ops = getattr(decisions.central_bank, "omo_ops", [])
        cb_updates, cb_ledgers, cb_log = central_bank.process_omo(
            world_state, tick=tick, day=day, omo_ops=omo_ops
        )
        updates.extend(cb_updates)
        ledgers.extend(cb_ledgers)
        logs.append(cb_log)
    except Exception:
        pass

    # 7) bond maturities
    try:
        from ..logic_modules import government_financial

        mat_updates, mat_ledgers, mat_log = (
            government_financial.process_bond_maturities(
                world_state, tick=tick, day=day
            )
        )
        updates.extend(mat_updates)
        ledgers.extend(mat_ledgers)
        logs.append(mat_log)
    except Exception:
        pass

    # collect market signals
    try:
        by = getattr(world_state.macro, "bond_yield", None)
        if by is not None:
            market_signals["bond_yield"] = float(by)
    except Exception:
        pass

    return updates, logs, ledgers, market_signals
