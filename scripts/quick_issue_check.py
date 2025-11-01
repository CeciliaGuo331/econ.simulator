import asyncio
from econ_sim.core.orchestrator import SimulationOrchestrator
from econ_sim.data_access.models import (
    TickDecisionOverrides,
    GovernmentDecisionOverride,
)


async def main():
    orch = SimulationOrchestrator()
    # Ensure simulation exists and has minimal core agents so baseline
    # fallback can generate decisions. Some dev test worlds may be sparse.
    state = await orch.create_simulation("test_world")
    from econ_sim.data_access.models import (
        AgentKind,
        StateUpdateCommand,
        FirmState,
        BankState,
        GovernmentState,
        MacroState,
    )

    updates = []
    if state.firm is None:
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.FIRM,
                agent_id="firm_1",
                balance_sheet={},
                price=10.0,
                wage_offer=80.0,
            )
        )
    if state.bank is None:
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.BANK,
                agent_id="bank",
                balance_sheet={},
                deposit_rate=0.01,
                loan_rate=0.05,
            )
        )
    if state.government is None:
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.GOVERNMENT,
                agent_id="government",
                balance_sheet={},
                tax_rate=0.15,
                spending=1000.0,
            )
        )
    if state.central_bank is None:
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.CENTRAL_BANK,
                agent_id="central_bank",
                balance_sheet={},
                base_rate=0.03,
                reserve_ratio=0.1,
            )
        )
    if updates:
        await orch.data_access.apply_updates("test_world", updates)
        # reload state reference
        state = await orch.get_state("test_world")
    # Ensure a couple of households exist and have cash to buy bonds
    await orch.data_access.ensure_entity_state("test_world", AgentKind.HOUSEHOLD, "1")
    await orch.data_access.ensure_entity_state("test_world", AgentKind.HOUSEHOLD, "2")
    # Now set their cash and empty bond holdings so bids are affordable
    hh_updates = []
    hh_updates.append(
        StateUpdateCommand.assign(
            AgentKind.HOUSEHOLD,
            agent_id=1,
            balance_sheet={"cash": 200.0},
            bond_holdings={},
        )
    )
    hh_updates.append(
        StateUpdateCommand.assign(
            AgentKind.HOUSEHOLD,
            agent_id=2,
            balance_sheet={"cash": 200.0},
            bond_holdings={},
        )
    )
    await orch.data_access.apply_updates("test_world", hh_updates)
    state = await orch.get_state("test_world")
    # Put issuance_plan both on the government override and at the top-level
    # TickDecisionOverrides. Some merge code paths prefer one location over
    # the other, so set both to be robust.
    gov_override = GovernmentDecisionOverride(
        issuance_plan={"volume": 30, "min_price": 0.5}
    )
    overrides = TickDecisionOverrides(
        government=gov_override,
        # also set the top-level issuance_plan so orchestrator/merge logic
        # definitely sees it regardless of how overrides are merged.
        issuance_plan={"volume": 30, "min_price": 0.5},
        bond_bids=[
            {"buyer_kind": "household", "buyer_id": 1, "price": 1.0, "quantity": 5},
            {"buyer_kind": "household", "buyer_id": 2, "price": 1.0, "quantity": 10},
        ],
    )
    print("Running one tick with proactive issuance...")

    # For quick testing we bypass the orchestrator's strict "require agent
    # coverage" check (which demands attached scripts for all agent kinds).
    # This is safe in a local smoke-test: we only want to exercise the
    # issuance/auction/persistence path.
    async def _noop_require_agent_coverage(sim_id: str) -> None:
        return None

    orch._require_agent_coverage = _noop_require_agent_coverage
    res = await orch.run_tick("test_world", overrides=overrides)
    print("Logs:")
    for l in res.logs:
        print("-", l.model_dump())
    ws = res.world_state
    print(
        "Government debt_instruments keys:", list(ws.government.debt_instruments.keys())
    )
    # print a sample household bond holdings
    for hid in (1, 2):
        try:
            hh = ws.households[hid]
            print(f"HH {hid} bond_holdings:", hh.bond_holdings)
        except Exception:
            print(f"HH {hid} not found")


if __name__ == "__main__":
    asyncio.run(main())
