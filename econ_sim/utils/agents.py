"""Utilities for mapping user roles to agent kinds."""

from __future__ import annotations

from typing import Optional

from ..data_access.models import AgentKind

_USER_TYPE_AGENT_KIND_MAP = {
    "individual": AgentKind.HOUSEHOLD,
    "firm": AgentKind.FIRM,
    "government": AgentKind.GOVERNMENT,
    "commercial_bank": AgentKind.BANK,
    "central_bank": AgentKind.CENTRAL_BANK,
}


def resolve_agent_kind(
    user_type: str,
    requested: Optional[AgentKind] = None,
    *,
    allow_override: bool = False,
) -> AgentKind:
    """Resolve the agent kind for a given user type.

    Parameters
    ----------
    user_type:
        The textual user role stored in the session or user profile.
    requested:
        Optional explicit agent kind requested by the caller. When ``allow_override``
        is False, the requested value must match the default mapping; otherwise a
        ``ValueError`` is raised. Administrators may set ``allow_override`` to
        True to bypass this restriction.
    allow_override:
        Whether to honour the ``requested`` value regardless of the default mapping.

    Returns
    -------
    AgentKind
        The resolved agent kind for the user.
    """

    normalized = (user_type or "").strip().lower()
    mapped = _USER_TYPE_AGENT_KIND_MAP.get(normalized)

    if mapped is None:
        if allow_override and requested is not None:
            return requested
        raise ValueError("Unsupported user type for agent kind resolution")

    if allow_override and requested is not None:
        return requested

    if requested is not None and requested != mapped:
        raise ValueError("Requested agent kind does not match user type")

    return mapped


def get_default_agent_kind(user_type: str) -> Optional[AgentKind]:
    """Return the default agent kind for a user type, or ``None`` if undefined."""

    return _USER_TYPE_AGENT_KIND_MAP.get((user_type or "").strip().lower())


__all__ = [
    "resolve_agent_kind",
    "get_default_agent_kind",
]
