"""Shared helpers for PostgreSQL-related operations."""

from __future__ import annotations

import re

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_identifier(identifier: str) -> str:
    """Safely quote a SQL identifier.

    Parameters
    ----------
    identifier:
        The identifier to quote. Must begin with a letter or underscore and
        contain only alphanumeric characters or underscores.
    """

    if not _IDENTIFIER_PATTERN.match(identifier):
        raise ValueError(f"Invalid SQL identifier: {identifier}")
    return f'"{identifier}"'


__all__ = ["quote_identifier"]
