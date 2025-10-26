"""金融市场子系统：处理 deposit / withdrawal /transfers 与记账。

提供简单的接口用于在模块间统一资金流逻辑，保证返回的 StateUpdateCommand
包含所有受影响主体的完整 balance_sheet 以便原子持久化。
"""

from __future__ import annotations

from typing import List, Tuple, Dict, Any

from ..data_access.models import (
    WorldState,
    StateUpdateCommand,
    LedgerEntry,
    AgentKind,
    TickLogEntry,
)


def _get_balance_sheet(
    world_state: WorldState, kind: AgentKind, entity_id: str
) -> Dict[str, Any]:
    """Return a mutable dict representing the entity's balance_sheet."""
    if kind is AgentKind.HOUSEHOLD:
        h = world_state.households[int(entity_id)]
        return h.balance_sheet.model_dump()
    elif kind is AgentKind.FIRM:
        f = world_state.firm
        return f.balance_sheet.model_dump()
    elif kind is AgentKind.GOVERNMENT:
        g = world_state.government
        return g.balance_sheet.model_dump()
    elif kind is AgentKind.BANK:
        b = world_state.bank
        return b.balance_sheet.model_dump()
    else:
        raise ValueError(f"Unsupported kind for balance_sheet access: {kind}")


def _assign_balance_sheet_updates(
    kind: AgentKind, entity_id: str, bs: Dict[str, Any]
) -> StateUpdateCommand:
    return StateUpdateCommand.assign(scope=kind, agent_id=entity_id, balance_sheet=bs)


def transfer(
    world_state: WorldState,
    payer_kind: AgentKind,
    payer_id: str,
    payee_kind: AgentKind,
    payee_id: str,
    amount: float,
    tick: int,
    day: int,
) -> Tuple[List[StateUpdateCommand], List[LedgerEntry], TickLogEntry]:
    """Transfer cash from payer to payee (cash payment).

    This updates in-memory world_state cash fields and returns StateUpdateCommand
    entries that set the full balance_sheet for both parties, plus ledger entries
    for auditing. It does NOT modify deposit fields (those change only on deposit/withdraw).
    """
    if amount <= 0:
        return (
            [],
            [],
            TickLogEntry(
                tick=tick, day=day, message="transfer_skipped", context={"amount": 0.0}
            ),
        )

    # mutate the actual world_state objects (so callers observing ws see changes)
    if payer_kind is AgentKind.HOUSEHOLD:
        payer = world_state.households[int(payer_id)]
        payer_cash = float(payer.balance_sheet.cash or 0.0)
    elif payer_kind is AgentKind.FIRM:
        payer = world_state.firm
        payer_cash = float(payer.balance_sheet.cash or 0.0)
    elif payer_kind is AgentKind.GOVERNMENT:
        payer = world_state.government
        payer_cash = float(payer.balance_sheet.cash or 0.0)
    elif payer_kind is AgentKind.BANK:
        payer = world_state.bank
        payer_cash = float(payer.balance_sheet.cash or 0.0)
    elif payer_kind is AgentKind.CENTRAL_BANK:
        payer = world_state.central_bank
        payer_cash = float(payer.balance_sheet.cash or 0.0)
    else:
        raise ValueError(f"Unsupported payer kind: {payer_kind}")

    if payee_kind is AgentKind.HOUSEHOLD:
        payee = world_state.households[int(payee_id)]
        payee_cash = float(payee.balance_sheet.cash or 0.0)
    elif payee_kind is AgentKind.FIRM:
        payee = world_state.firm
        payee_cash = float(payee.balance_sheet.cash or 0.0)
    elif payee_kind is AgentKind.GOVERNMENT:
        payee = world_state.government
        payee_cash = float(payee.balance_sheet.cash or 0.0)
    elif payee_kind is AgentKind.BANK:
        payee = world_state.bank
        payee_cash = float(payee.balance_sheet.cash or 0.0)
    elif payee_kind is AgentKind.CENTRAL_BANK:
        payee = world_state.central_bank
        payee_cash = float(payee.balance_sheet.cash or 0.0)
    else:
        raise ValueError(f"Unsupported payee kind: {payee_kind}")

    transfer_amount = float(amount)
    # central bank may create reserves / money; allow unlimited transfer from central bank
    if payer_kind is AgentKind.CENTRAL_BANK:
        actual = transfer_amount
    else:
        actual = min(transfer_amount, payer_cash)

    payer_cash_after = payer_cash - actual
    payee_cash_after = payee_cash + actual

    # write back into world_state
    payer.balance_sheet.cash = payer_cash_after
    payee.balance_sheet.cash = payee_cash_after

    updates: List[StateUpdateCommand] = []
    ledgers: List[LedgerEntry] = []

    updates.append(
        _assign_balance_sheet_updates(
            payer_kind, payer_id, payer.balance_sheet.model_dump()
        )
    )
    updates.append(
        _assign_balance_sheet_updates(
            payee_kind, payee_id, payee.balance_sheet.model_dump()
        )
    )

    ledgers.append(
        LedgerEntry(
            tick=tick,
            day=day,
            account_kind=payer_kind,
            entity_id=str(payer_id),
            entry_type="transfer_out",
            amount=-actual,
            balance_after=payer_cash_after,
        )
    )
    ledgers.append(
        LedgerEntry(
            tick=tick,
            day=day,
            account_kind=payee_kind,
            entity_id=str(payee_id),
            entry_type="transfer_in",
            amount=actual,
            balance_after=payee_cash_after,
        )
    )

    log = TickLogEntry(
        tick=tick,
        day=day,
        message="cash_transfer",
        context={
            "payer": str(payer_id),
            "payee": str(payee_id),
            "amount": float(actual),
        },
    )

    return updates, ledgers, log


def deposit(
    world_state: WorldState,
    household_id: int,
    bank_id: str,
    amount: float,
    tick: int,
    day: int,
) -> Tuple[List[StateUpdateCommand], List[LedgerEntry], TickLogEntry]:
    """Household deposits cash into bank: cash -> deposits.

    Bank.deposits and bank.reserves are increased accordingly (reserves += amount).
    """
    if amount <= 0:
        return (
            [],
            [],
            TickLogEntry(
                tick=tick, day=day, message="deposit_skipped", context={"amount": 0.0}
            ),
        )

    hh = world_state.households[household_id]
    bank = world_state.bank

    available = float(hh.balance_sheet.cash or 0.0)
    actual = min(float(amount), available)
    if actual <= 0:
        return (
            [],
            [],
            TickLogEntry(
                tick=tick, day=day, message="deposit_failed_no_cash", context={}
            ),
        )

    # mutate in-memory
    hh.balance_sheet.cash = float(hh.balance_sheet.cash) - actual
    hh.balance_sheet.deposits = float(hh.balance_sheet.deposits or 0.0) + actual

    bank.balance_sheet.deposits = float(bank.balance_sheet.deposits or 0.0) + actual
    # increase reserves by full cash deposit (simplified)
    bank.balance_sheet.reserves = float(bank.balance_sheet.reserves or 0.0) + actual

    updates: List[StateUpdateCommand] = []
    ledgers: List[LedgerEntry] = []

    updates.append(
        _assign_balance_sheet_updates(
            AgentKind.HOUSEHOLD, household_id, hh.balance_sheet.model_dump()
        )
    )
    updates.append(
        _assign_balance_sheet_updates(
            AgentKind.BANK, bank.id, bank.balance_sheet.model_dump()
        )
    )

    ledgers.append(
        LedgerEntry(
            tick=tick,
            day=day,
            account_kind=AgentKind.HOUSEHOLD,
            entity_id=str(household_id),
            entry_type="deposit",
            amount=-actual,
            balance_after=hh.balance_sheet.cash,
        )
    )
    ledgers.append(
        LedgerEntry(
            tick=tick,
            day=day,
            account_kind=AgentKind.BANK,
            entity_id=bank.id,
            entry_type="deposit_received",
            amount=actual,
            balance_after=bank.balance_sheet.deposits,
        )
    )

    log = TickLogEntry(
        tick=tick,
        day=day,
        message="deposit_executed",
        context={"household": household_id, "bank": bank.id, "amount": actual},
    )

    return updates, ledgers, log


def withdraw(
    world_state: WorldState,
    household_id: int,
    bank_id: str,
    amount: float,
    tick: int,
    day: int,
) -> Tuple[List[StateUpdateCommand], List[LedgerEntry], TickLogEntry]:
    """Household withdraws from deposits to cash.

    bank.deposits and bank.reserves decrease accordingly.
    """
    if amount <= 0:
        return (
            [],
            [],
            TickLogEntry(
                tick=tick, day=day, message="withdraw_skipped", context={"amount": 0.0}
            ),
        )

    hh = world_state.households[household_id]
    bank = world_state.bank

    avail_dep = float(hh.balance_sheet.deposits or 0.0)
    actual = min(float(amount), avail_dep)
    if actual <= 0:
        return (
            [],
            [],
            TickLogEntry(
                tick=tick, day=day, message="withdraw_failed_no_deposits", context={}
            ),
        )

    # mutate in-memory
    hh.balance_sheet.deposits = float(hh.balance_sheet.deposits or 0.0) - actual
    hh.balance_sheet.cash = float(hh.balance_sheet.cash or 0.0) + actual

    bank.balance_sheet.deposits = float(bank.balance_sheet.deposits or 0.0) - actual
    bank.balance_sheet.reserves = float(bank.balance_sheet.reserves or 0.0) - actual

    updates: List[StateUpdateCommand] = []
    ledgers: List[LedgerEntry] = []

    updates.append(
        _assign_balance_sheet_updates(
            AgentKind.HOUSEHOLD, household_id, hh.balance_sheet.model_dump()
        )
    )
    updates.append(
        _assign_balance_sheet_updates(
            AgentKind.BANK, bank.id, bank.balance_sheet.model_dump()
        )
    )

    ledgers.append(
        LedgerEntry(
            tick=tick,
            day=day,
            account_kind=AgentKind.HOUSEHOLD,
            entity_id=str(household_id),
            entry_type="withdraw",
            amount=actual,
            balance_after=hh.balance_sheet.cash,
        )
    )
    ledgers.append(
        LedgerEntry(
            tick=tick,
            day=day,
            account_kind=AgentKind.BANK,
            entity_id=bank.id,
            entry_type="withdraw_paid",
            amount=-actual,
            balance_after=bank.balance_sheet.deposits,
        )
    )

    log = TickLogEntry(
        tick=tick,
        day=day,
        message="withdraw_executed",
        context={"household": household_id, "bank": bank.id, "amount": actual},
    )

    return updates, ledgers, log
