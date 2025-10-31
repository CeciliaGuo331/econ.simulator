from econ_sim.script_engine.registry import ScriptRegistry, _ScriptRecord
from econ_sim.script_engine.registry import ScriptMetadata
from econ_sim.data_access.models import (
    TickDecisionOverrides,
    HouseholdDecisionOverride,
    WorldState,
    FirmState,
    MacroState,
    HouseholdState,
    BalanceSheet,
    AgentKind,
)
from econ_sim.utils.settings import get_world_config
from datetime import datetime


def test_sanitize_household_override_clips_consumption_budget():
    cfg = get_world_config()
    # build minimal world with firm price
    ws = WorldState(
        simulation_id="s",
        tick=1,
        day=1,
        households={},
        firm=FirmState(),
        bank=None,
        government=None,
        central_bank=None,
        macro=MacroState(),
    )
    ws.firm.price = 10.0
    # create a household override with zero consumption_budget
    hh_ov = HouseholdDecisionOverride()
    hh_ov.consumption_budget = 0.0
    hh_ov.savings_rate = -0.5
    hh_ov.labor_supply = 2.0

    overrides = TickDecisionOverrides(households={0: hh_ov})

    # construct a fake record
    meta = ScriptMetadata(
        script_id="s1",
        simulation_id="s",
        user_id="u",
        description=None,
        created_at=datetime.utcnow(),
        code_version="v",
        agent_kind=AgentKind.HOUSEHOLD,
        entity_id="0",
    )
    rec = _ScriptRecord(metadata=meta, code="def generate_decisions(c): pass")
    reg = ScriptRegistry()
    # call sanitizer
    reg._sanitize_overrides(rec, overrides, ws, cfg)

    subsistence = float(cfg.markets.goods.subsistence_consumption)
    expected_min = subsistence * float(ws.firm.price)

    out = overrides.households[0]
    assert float(out.consumption_budget) >= expected_min
    assert 0.0 <= float(out.savings_rate) <= 1.0
    assert 0.0 <= float(out.labor_supply) <= 1.0
