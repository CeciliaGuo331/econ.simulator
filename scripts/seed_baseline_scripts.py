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
from pathlib import Path
from typing import Iterable, Optional

from econ_sim.script_engine import script_registry
from econ_sim.script_engine.registry import ScriptExecutionError

BASELINE_DIR = Path(__file__).resolve().parents[1] / "deploy" / "baseline_scripts"

BASELINE_DEFINITIONS = (
    {
        "user_id": "baseline.household@econ.sim",
        "path": BASELINE_DIR / "household_baseline.py",
        "description": "[baseline] Household reference strategy",
    },
    {
        "user_id": "baseline.firm@econ.sim",
        "path": BASELINE_DIR / "firm_baseline.py",
        "description": "[baseline] Firm reference strategy",
    },
    {
        "user_id": "baseline.bank@econ.sim",
        "path": BASELINE_DIR / "bank_baseline.py",
        "description": "[baseline] Commercial bank reference strategy",
    },
    {
        "user_id": "baseline.central_bank@econ.sim",
        "path": BASELINE_DIR / "central_bank_baseline.py",
        "description": "[baseline] Central bank reference strategy",
    },
    {
        "user_id": "baseline.government@econ.sim",
        "path": BASELINE_DIR / "government_baseline.py",
        "description": "[baseline] Government reference strategy",
    },
)


async def _load_script(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Baseline script not found: {path}")
    return path.read_text(encoding="utf-8")


async def _register_all(
    simulation_id: Optional[str],
    attach: bool,
    overwrite: bool,
) -> list[str]:
    registry = script_registry
    created: list[str] = []

    for entry in BASELINE_DEFINITIONS:
        user_id = entry["user_id"]
        code = await _load_script(entry["path"])

        if overwrite:
            # Remove any existing scripts for the baseline user to keep idempotent runs.
            await registry.remove_scripts_by_user(user_id)

        metadata = await registry.register_script(
            simulation_id=simulation_id if attach else None,
            user_id=user_id,
            script_code=code,
            description=entry["description"],
        )
        created.append(metadata.script_id)

        if attach and simulation_id and metadata.simulation_id != simulation_id:
            await registry.attach_script(metadata.script_id, simulation_id, user_id)

    return created


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
        created = await _register_all(args.simulation_id, args.attach, args.overwrite)
    except (FileNotFoundError, ScriptExecutionError) as exc:
        parser.exit(1, f"[seed] failed: {exc}\n")

    for script_id in created:
        print(f"[seed] created script {script_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
