"""Seed baseline user strategy scripts into the script registry.

Usage (inside Docker container or local virtualenv):

    python scripts/seed_baseline_scripts.py --simulation demo-sim --attach

The script will clean up existing scripts created for the baseline users and
re-register them using the sandbox-aware registry. When `--attach` is provided,
new scripts are immediately mounted to the specified simulation ID.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Iterable, Optional

from econ_sim.script_engine import script_registry
from econ_sim.script_engine.baseline_seed import ensure_baseline_scripts


async def _register_all(
    simulation_id: Optional[str],
    attach: bool,
    overwrite: bool,
) -> dict[str, list[str]]:
    return await ensure_baseline_scripts(
        script_registry,
        attach_to_simulation=simulation_id if attach else None,
        overwrite=overwrite,
        strict=True,
    )


async def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed baseline user strategies into the registry"
    )
    parser.add_argument(
        "--simulation",
        dest="simulation_id",
        help="Simulation ID to attach the baseline scripts to (optional)",
    )
    parser.add_argument(
        "--attach",
        action="store_true",
        help="Attach newly created scripts to the provided simulation ID",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Remove existing baseline scripts before seeding to keep the operation idempotent",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.attach and not args.simulation_id:
        parser.error("--attach requires --simulation to be specified")

    try:
        summary = await _register_all(args.simulation_id, args.attach, args.overwrite)
    except Exception as exc:
        parser.exit(1, f"[seed] failed: {exc}\n")

    for script_id in summary.get("created", []):
        print(f"[seed] created script {script_id}")
    for script_id in summary.get("attached", []):
        print(f"[seed] attached script {script_id}")
    skipped = summary.get("skipped_users", [])
    if skipped:
        print("[seed] skipped users with existing scripts: " + ", ".join(skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
