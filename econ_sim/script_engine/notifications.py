"""Utilities for dispatching script execution failure notifications."""

from __future__ import annotations

import logging
from typing import Protocol

from .registry import ScriptFailureEvent

logger = logging.getLogger(__name__)


class ScriptFailureNotifier(Protocol):
    """Protocol describing notification dispatchers for script failures."""

    def notify(self, event: ScriptFailureEvent) -> None:  # pragma: no cover - interface
        """Deliver a script failure event to interested parties."""


class LoggingScriptFailureNotifier:
    """Default notifier that records failures via application logs."""

    def notify(self, event: ScriptFailureEvent) -> None:
        logger.error(
            "Script failure notification | simulation=%s | user=%s | script=%s | agent=%s | entity=%s | message=%s",
            event.simulation_id,
            event.user_id,
            event.script_id,
            event.agent_kind.value,
            event.entity_id,
            event.message,
            exc_info=False,
        )
        logger.debug(
            "Script failure traceback for %s:\n%s",
            event.script_id,
            event.traceback,
        )


__all__ = ["ScriptFailureNotifier", "LoggingScriptFailureNotifier"]
