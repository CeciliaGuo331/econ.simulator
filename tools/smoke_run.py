# Minimal smoke test for run_tick_new
import json
import sys

try:
    from econ_sim.core.orchestrator import run_tick_new
    from econ_sim.data_access.models import (
        WorldState,
        HouseholdState,
        BalanceSheet,
        FirmState,
        BankState,
        GovernmentState,
        CentralBankState,
        MacroState,
    )
except Exception as e:
    print("IMPORT_ERROR", e)
    raise

# construct minimal world
hh = HouseholdState(id=1, balance_sheet=BalanceSheet(cash=100.0))
firm = FirmState()
bank = BankState()
gov = GovernmentState()
cb = CentralBankState()
macro = MacroState()
world = WorldState(
    simulation_id="smoke",
    tick=1,
    day=1,
    households={1: hh},
    firm=firm,
    bank=bank,
    government=gov,
    central_bank=cb,
    macro=macro,
)

try:
    updates, logs, ledgers, market_signals = run_tick_new(world)
    print("OK")
    print("updates:", len(updates))
    print("logs:", len(logs))
    print("ledgers:", len(ledgers))
    print("market_signals:", json.dumps(market_signals))
except Exception as e:
    print("RUNTIME_ERROR", e)
    import traceback

    traceback.print_exc()
    sys.exit(2)
