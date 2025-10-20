"""脚本执行失败通知工具。

本模块定义通知接口（Protocol）与默认的日志记录通知实现。它用于在脚本执行
失败时将失败事件分发到感兴趣的接收方（例如邮件、监控队列或日志系统）。

接口要点：
- `ScriptFailureNotifier`：通知器协议，需实现 `notify(event)` 方法；
- `LoggingScriptFailureNotifier`：默认实现，将失败信息以结构化日志写入应用日志，
    包含仿真 ID、用户、脚本 ID、代理类型、实体 ID 及失败堆栈跟踪，便于离线排查。

扩展点：可在运行时通过依赖注入或配置替换为更复杂的通知实现（如发送 Slack/邮件/报警）。
"""

from __future__ import annotations

import logging
from typing import Protocol

from .registry import ScriptFailureEvent

logger = logging.getLogger(__name__)


class ScriptFailureNotifier(Protocol):
    """描述脚本失败通知器接口的协议（Protocol）。"""

    def notify(self, event: ScriptFailureEvent) -> None:  # pragma: no cover - interface
        """将脚本失败事件发送给感兴趣的接收方。"""


class LoggingScriptFailureNotifier:
    """默认的通知实现：通过应用日志记录失败信息（结构化日志）。"""

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
