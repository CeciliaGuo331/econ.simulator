"""Command line entrypoint to seed the canonical "test_world" simulation."""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from typing import Iterable, Optional

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from econ_sim.script_engine.test_world_seed import seed_test_world


async def _run(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the test_world simulation")
    parser.add_argument(
        "--simulation-id",
        dest="simulation_id",
        default=None,
        help="Override the default simulation identifier (test_world)",
    )
    parser.add_argument(
        "--households",
        type=int,
        default=None,
        help="Requested number of household scripts to seed (minimum enforced: 400)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Delete existing scripts for seeded users before uploading",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    summary = await seed_test_world(
        simulation_id=args.simulation_id or "test_world",
        household_count=args.households if args.households is not None else 400,
        overwrite_existing=args.overwrite,
    )

    print(
        "Seeded simulation %s | users=%s (created=%s existing=%s) | scripts=%s (created=%s existing=%s)"
        % (
            summary.simulation_id,
            summary.total_users,
            summary.users_created,
            summary.users_existing,
            summary.total_scripts,
            summary.scripts_created,
            summary.scripts_existing,
        )
    )
    return 0


def main(argv: Optional[Iterable[str]] = None) -> int:
    return asyncio.run(_run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
