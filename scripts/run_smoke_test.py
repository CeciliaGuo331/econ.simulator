"""Run a lightweight smoke test to exercise the orchestrator and sandbox.

This script is intended to be run with the project's Python environment activated
(e.g. `conda activate econsim`). It will:
- create or ensure a simulation instance named `smoke-test` exists
- run a single day with 1 tick
- print elapsed wall-clock time and the sandbox metrics

"""

import asyncio
import time
import sys
from pathlib import Path

# allow running from repo root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from econ_sim.core.orchestrator import SimulationOrchestrator

from econ_sim.script_engine.sandbox import get_sandbox_metrics
from econ_sim.script_engine.baseline_seed import ensure_baseline_scripts
from econ_sim.script_engine import script_registry


async def _run():
    orchestrator = SimulationOrchestrator()
    sim_id = "smoke-test"
    print("Creating/ensuring simulation:", sim_id)
    await orchestrator.create_simulation(sim_id)
    print("Seeding baseline scripts into simulation...")
    summary = await ensure_baseline_scripts(
        script_registry, attach_to_simulation=sim_id, overwrite=True
    )
    print("Seed summary:", summary)
    # If ensure_baseline_scripts didn't attach (older logic paths), attempt to attach created scripts explicitly
    if not summary.get("attached") and summary.get("created"):
        all_meta = await script_registry.list_all_scripts()
        meta_map = {m.script_id: m for m in all_meta}
        attached = []
        for sid in summary.get("created", []):
            meta = meta_map.get(sid)
            if not meta:
                continue
            try:
                updated = await script_registry.attach_script(
                    meta.script_id, sim_id, meta.user_id
                )
                attached.append(updated.script_id)
            except Exception:
                pass
        if attached:
            print("Explicitly attached scripts:", attached)
        # Ensure entity state exists for attached scripts so orchestrator coverage checks pass
        try:
            scripts = await script_registry.list_scripts(sim_id)
            for meta in scripts:
                try:
                    await orchestrator._ensure_entity_seeded(meta)
                except Exception:
                    pass
            print("Ensured entity states for attached scripts.")
        except Exception:
            pass
    # run a single day with 1 tick
    start = time.time()
    result = await orchestrator.run_day(sim_id, ticks_per_day=1)
    elapsed = time.time() - start
    print(
        f"Smoke test run completed: elapsed_sec={elapsed:.4f}, ticks_executed={result.ticks_executed}"
    )
    try:
        metrics = get_sandbox_metrics()
        print("Sandbox metrics:")
        for k, v in metrics.items():
            print(f"  {k}: {v}")
    except Exception as exc:
        print("Failed to read sandbox metrics:", exc)


if __name__ == "__main__":
    asyncio.run(_run())
