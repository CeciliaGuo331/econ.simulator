import pytest

from econ_sim.data_access.models import (
    WorldState,
    HouseholdState,
    BalanceSheet,
    BankState,
    FirmState,
    GovernmentState,
    MacroState,
)
from econ_sim.data_access.redis_client import DataAccessLayer
from econ_sim.utils.settings import WorldConfig


def make_world():
    ws = WorldState(
        simulation_id="recon_test",
        tick=1,
        day=0,
        households={},
        firm=FirmState(id="firm_1", balance_sheet=BalanceSheet(cash=0.0, deposits=0.0)),
        bank=BankState(id="bank", balance_sheet=BalanceSheet(cash=0.0, deposits=10.0)),
        government=GovernmentState(
            id="government", balance_sheet=BalanceSheet(cash=0.0, deposits=0.0)
        ),
        central_bank=None,
        macro=MacroState(),
    )
    ws.households[1] = HouseholdState(
        id=1, balance_sheet=BalanceSheet(cash=100.0, deposits=40.0)
    )
    ws.households[2] = HouseholdState(
        id=2, balance_sheet=BalanceSheet(cash=50.0, deposits=60.0)
    )
    # nonbank deposits = 40 + 60 + firm 0 + gov 0 = 100
    return ws


@pytest.mark.asyncio
async def test_auto_reconcile_corrects_bank_deposits():
    ws = make_world()
    # persist initial inconsistent state via DataAccessLayer store
    cfg = WorldConfig()
    dal = DataAccessLayer.with_default_store(cfg)
    # directly write world snapshot to configured store
    await dal.store.store(ws.simulation_id, ws.model_dump())

    # apply no-op updates; reconciliation should run during apply_updates
    updated = await dal.apply_updates(ws.simulation_id, updates=[])

    assert updated.bank.balance_sheet.deposits == pytest.approx(100.0)
    # tick logs should contain a reconciliation entry
    logs = await dal.get_recent_logs(ws.simulation_id)
    assert any(l.message == "deposit_reconciled" for l in logs)
    # persisted world state should reflect corrected bank deposits
    payload = await dal.store.load(ws.simulation_id)
    assert payload is not None
    bank_payload = payload.get("bank", {}) or {}
    assert float(
        bank_payload.get("balance_sheet", {}).get("deposits", 0.0)
    ) == pytest.approx(100.0)
