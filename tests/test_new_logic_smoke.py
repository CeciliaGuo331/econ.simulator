"""Smoke test for the new_logic minimal flow.

This test constructs a minimal WorldState using existing factory helpers and
runs a single tick through `new_logic.run_tick_new`, asserting that updates
and logs are produced.
"""

from __future__ import annotations

from econ_sim.core import entity_factory
from econ_sim.data_access.models import (
    WorldState,
    MacroState,
    SimulationFeatures,
    AgentKind,
)
from econ_sim.core.orchestrator import run_tick_new


def make_minimal_world(household_count: int = 5) -> WorldState:
    from econ_sim.utils.settings import get_world_config

    cfg = get_world_config()
    households = {
        hid: entity_factory.create_household_state(cfg, hid)
        for hid in range(1, household_count + 1)
    }
    firm = entity_factory.create_firm_state(cfg, "firm_1")
    bank = entity_factory.create_bank_state(cfg, "bank", households)
    government = entity_factory.create_government_state(cfg, "government")
    central_bank = entity_factory.create_central_bank_state(cfg, "central_bank")
    macro = entity_factory.create_macro_state()

    ws = WorldState(
        simulation_id="smoke_sim",
        tick=0,
        day=0,
        households=households,
        firm=firm,
        bank=bank,
        government=government,
        central_bank=central_bank,
        macro=macro,
        features=SimulationFeatures(),
    )
    return ws


def test_run_tick_new_smoke():
    ws = make_minimal_world()
    updates, logs, ledgers, market_signals = run_tick_new(ws)
    assert isinstance(updates, list)
    assert len(logs) > 0
    # expect at least one update (macro or other)
    assert any(isinstance(u, object) for u in updates)
