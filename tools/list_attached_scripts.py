#!/usr/bin/env python3
"""Utility: list scripts attached to a simulation and show their metadata.

Usage: python tools/list_attached_scripts.py <simulation_id>

This imports the project package and calls the ScriptRegistry to list scripts.
It requires the environment to be set up (e.g., ECON_SIM_POSTGRES_DSN if using Postgres
backing store). Run inside the project virtualenv.
"""
import sys
import asyncio
from pprint import pprint

from econ_sim.script_engine import get_script_registry


async def main(sim_id: str):
    reg = get_script_registry()
    metas = await reg.list_scripts(sim_id)
    if not metas:
        print(f"No scripts attached to simulation {sim_id}")
        return
    for m in metas:
        print("---")
        print(f"script_id: {m.script_id}")
        print(f"user_id: {m.user_id}")
        print(f"agent_kind: {m.agent_kind}")
        print(f"entity_id: {m.entity_id}")
        print(f"created_at: {m.created_at}")
        print(f"description: {m.description}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tools/list_attached_scripts.py <simulation_id>")
        sys.exit(1)
    sim = sys.argv[1]
    asyncio.run(main(sim))
