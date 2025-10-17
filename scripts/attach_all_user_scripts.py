"""Bulk attach all user scripts to a simulation.

Usage:

    python scripts/attach_all_user_scripts.py --simulation demo-sim --only-unmounted

Optional flags:
    --force-reset   Reset the simulation to tick 0 before attaching (required if not at tick 0).
    --dry-run       Show what would be attached without making changes.

Run this inside Docker or your local venv (same as other management scripts).
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Iterable, Optional

import sys
from pathlib import Path

# Make project root importable when running as a script
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from econ_sim.core.orchestrator import (
    SimulationNotFoundError,
    SimulationOrchestrator,
    SimulationStateError,
)
from econ_sim.script_engine import script_registry
from econ_sim.script_engine.registry import ScriptExecutionError


async def _attach_all(
    simulation_id: str,
    *,
    only_unmounted: bool,
    force_reset: bool,
    dry_run: bool,
) -> dict[str, int]:
    orchestrator = SimulationOrchestrator()

    try:
        state = await orchestrator.get_state(simulation_id)
    except SimulationNotFoundError:
        state = await orchestrator.create_simulation(simulation_id)

    if state.tick != 0:
        if not force_reset:
            raise SimulationStateError(simulation_id, state.tick)
        state = await orchestrator.reset_simulation(simulation_id)

    # Collect targets
    all_scripts = await script_registry.list_all_scripts()
    targets = [
        m
        for m in all_scripts
        if (not only_unmounted) or (m.simulation_id is None)
    ]

    attempted = 0
    attached = 0
    skipped = 0
    failed = 0

    for meta in targets:
        if dry_run:
            print(f"[dry-run] would attach {meta.script_id} (user={meta.user_id})")
            attempted += 1
            continue
        try:
            await orchestrator.attach_script_to_simulation(
                simulation_id=simulation_id,
                script_id=meta.script_id,
                user_id=meta.user_id,
            )
        except ScriptExecutionError as exc:
            print(
                f"[skip] {meta.script_id} (user={meta.user_id}): {exc}",
                flush=True,
            )
            failed += 1
        else:
            print(
                f"[attached] {meta.script_id} (kind={meta.agent_kind.value}, entity_id={meta.entity_id})",
                flush=True,
            )
            attached += 1
        finally:
            attempted += 1

    return {
        "attempted": attempted,
        "attached": attached,
        "failed": failed,
        "skipped": skipped,
    }


async def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Bulk attach user scripts")
    parser.add_argument(
        "--simulation",
        required=True,
        help="Simulation ID to attach scripts to",
    )
    parser.add_argument(
        "--only-unmounted",
        action="store_true",
        help="Only attach scripts not currently bound to any simulation",
    )
    parser.add_argument(
        "--force-reset",
        action="store_true",
        help="Reset simulation to tick 0 if not at tick 0",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be attached without making changes",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        summary = await _attach_all(
            args.simulation,
            only_unmounted=args.only_unmounted,
            force_reset=args.force_reset,
            dry_run=args.dry_run,
        )
    except SimulationStateError as exc:
        parser.exit(
            2,
            (
                f"[abort] simulation {exc.simulation_id} at tick {exc.tick}. "
                "Use --force-reset to reset to tick 0 before attaching.\n"
            ),
        )
    except Exception as exc:  # pragma: no cover - safety
        parser.exit(1, f"[attach] failed: {exc}\n")

    print(
        (
            f"[summary] attempted={summary['attempted']} "
            f"attached={summary['attached']} failed={summary['failed']}"
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
