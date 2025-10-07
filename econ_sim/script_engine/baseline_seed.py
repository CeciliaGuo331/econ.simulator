"""Utility helpers to seed baseline strategy scripts for default users."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from ..data_access.models import AgentKind
from .registry import ScriptExecutionError, ScriptMetadata, ScriptRegistry

logger = logging.getLogger(__name__)

BASELINE_DIR = Path(__file__).resolve().parents[2] / "deploy" / "baseline_scripts"


@dataclass(frozen=True)
class BaselineScriptDefinition:
    user_id: str
    filename: str
    description: str
    agent_kind: AgentKind
    entity_id: str

    @property
    def path(self) -> Path:
        return BASELINE_DIR / self.filename


BASELINE_DEFINITIONS: Sequence[BaselineScriptDefinition] = (
    BaselineScriptDefinition(
        user_id="baseline.household@econ.sim",
        filename="household_baseline.py",
        description="[baseline] Household reference strategy",
        agent_kind=AgentKind.HOUSEHOLD,
        entity_id="baseline_household",
    ),
    BaselineScriptDefinition(
        user_id="baseline.firm@econ.sim",
        filename="firm_baseline.py",
        description="[baseline] Firm reference strategy",
        agent_kind=AgentKind.FIRM,
        entity_id="baseline_firm",
    ),
    BaselineScriptDefinition(
        user_id="baseline.bank@econ.sim",
        filename="bank_baseline.py",
        description="[baseline] Commercial bank reference strategy",
        agent_kind=AgentKind.BANK,
        entity_id="baseline_bank",
    ),
    BaselineScriptDefinition(
        user_id="baseline.central_bank@econ.sim",
        filename="central_bank_baseline.py",
        description="[baseline] Central bank reference strategy",
        agent_kind=AgentKind.CENTRAL_BANK,
        entity_id="baseline_central_bank",
    ),
    BaselineScriptDefinition(
        user_id="baseline.government@econ.sim",
        filename="government_baseline.py",
        description="[baseline] Government reference strategy",
        agent_kind=AgentKind.GOVERNMENT,
        entity_id="baseline_government",
    ),
)


def _load_script(definition: BaselineScriptDefinition) -> str:
    path = definition.path
    if not path.exists():
        raise FileNotFoundError(f"Baseline script not found: {path}")
    raw = path.read_text(encoding="utf-8")
    lines: List[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("from __future__ import"):
            continue
        if stripped.startswith("from typing import"):
            continue
        lines.append(line)
    code = "\n".join(lines)
    code = code.replace("context: Dict[str, Any]", "context")
    code = code.replace(") -> Dict[str, Any]:", "):")
    if raw.endswith("\n") and not code.endswith("\n"):
        code += "\n"
    return code


async def ensure_baseline_scripts(
    registry: ScriptRegistry,
    *,
    attach_to_simulation: Optional[str] = None,
    overwrite: bool = False,
    strict: bool = False,
) -> Dict[str, List[str]]:
    """Ensure all baseline strategy scripts exist for their default users.

    Parameters
    ----------
    registry:
        Target script registry instance.
    attach_to_simulation:
        When provided, baseline scripts (existing or newly created) are attached
        to the given simulation identifier.
    overwrite:
        When True, any existing scripts owned by the baseline users are removed
        before seeding new copies.

    Returns
    -------
    Dict[str, List[str]]
        Summary dictionary containing IDs of created scripts (``created``),
        scripts that were attached to the provided simulation (``attached``),
        and baseline users whose existing scripts were left untouched
        (``skipped_users``).
    """

    summary: Dict[str, List[str]] = {
        "created": [],
        "attached": [],
        "skipped_users": [],
        "errors": [],
    }

    for definition in BASELINE_DEFINITIONS:
        user_id = definition.user_id

        if overwrite:
            removed = await registry.remove_scripts_by_user(user_id)
            if removed:
                logger.info(
                    "Removed %s existing scripts for baseline user %s",
                    removed,
                    user_id,
                )
            existing: List[ScriptMetadata] = []
        else:
            existing = [
                script
                for script in await registry.list_user_scripts(user_id)
                if script.agent_kind == definition.agent_kind
                and script.entity_id == definition.entity_id
            ]

        if not existing:
            try:
                code = _load_script(definition)
            except FileNotFoundError as exc:
                message = f"Baseline script file missing for {user_id}: {exc}"
                summary["errors"].append(message)
                logger.error(message)
                if strict:
                    raise
                continue

            try:
                metadata = await registry.register_script(
                    simulation_id=(
                        attach_to_simulation if attach_to_simulation else None
                    ),
                    user_id=user_id,
                    script_code=code,
                    description=definition.description,
                    agent_kind=definition.agent_kind,
                    entity_id=definition.entity_id,
                )
            except ScriptExecutionError as exc:
                message = f"Failed to register baseline script for {user_id}: {exc}"
                summary["errors"].append(message)
                logger.error(message)
                if strict:
                    raise
                continue

            summary["created"].append(metadata.script_id)
            existing = [metadata]
        else:
            summary["skipped_users"].append(user_id)

        if attach_to_simulation:
            already_attached = any(
                script.simulation_id == attach_to_simulation for script in existing
            )
            if not already_attached:
                # Prefer attaching the most recent script (last item in sorted list).
                latest = existing[-1]
                try:
                    updated = await registry.attach_script(
                        latest.script_id, attach_to_simulation, user_id
                    )
                except ScriptExecutionError as exc:
                    message = (
                        "Failed to attach baseline script "
                        f"{latest.script_id} to {attach_to_simulation}: {exc}"
                    )
                    summary["errors"].append(message)
                    logger.error(message)
                    if strict:
                        raise
                else:
                    summary["attached"].append(updated.script_id)

    return summary


__all__ = [
    "ensure_baseline_scripts",
    "BASELINE_DEFINITIONS",
    "BaselineScriptDefinition",
]
