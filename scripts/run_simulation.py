from __future__ import annotations

import asyncio

from econ_sim.core.orchestrator import SimulationOrchestrator


async def main() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "demo"
    state = await orchestrator.create_simulation(simulation_id)
    print(f"Created simulation '{simulation_id}' at tick {state.tick}, day {state.day}")

    for _ in range(3):
        result = await orchestrator.run_tick(simulation_id)
        macro = result.world_state.macro
        print(
            f"Tick {result.world_state.tick} complete: GDP={macro.gdp:.2f}, "
            f"inflation={macro.inflation:.4f}, unemployment={macro.unemployment_rate:.3f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
