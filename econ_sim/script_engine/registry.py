"""管理用户上传脚本并在仿真过程中执行的注册中心。"""

from __future__ import annotations

import ast
import asyncio
import logging
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import (
    Dict,
    Iterable,
    List,
    Optional,
    Protocol,
    Set,
    TYPE_CHECKING,
)
from pydantic import BaseModel, ValidationError

from ..data_access.models import (
    AgentKind,
    TickDecisionOverrides,
    TickLogEntry,
    WorldState,
)
from ..logic_modules.agent_logic import merge_tick_overrides
from ..utils.settings import WorldConfig
from .sandbox import (
    ALLOWED_MODULES,
    DEFAULT_SANDBOX_TIMEOUT,
    ScriptSandboxError,
    ScriptSandboxTimeout,
    execute_script,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .postgres_store import StoredScript


logger = logging.getLogger(__name__)


class ScriptExecutionError(RuntimeError):
    """在脚本编译或执行阶段抛出的异常。"""


@dataclass(slots=True)
class ScriptFailureEvent:
    """记录单次脚本执行失败，用于后续通知与排查。"""

    script_id: str
    simulation_id: str
    user_id: str
    agent_kind: AgentKind
    entity_id: str
    message: str
    traceback: str
    occurred_at: datetime


class ScriptMetadata(BaseModel):
    """描述脚本的基本信息，便于前端展示与管理。"""

    script_id: str
    simulation_id: Optional[str] = None
    user_id: str
    description: Optional[str] = None
    created_at: datetime
    code_version: str
    agent_kind: AgentKind
    entity_id: str
    last_failure_at: Optional[datetime] = None
    last_failure_reason: Optional[str] = None


@dataclass
class _ScriptRecord:
    metadata: ScriptMetadata
    code: str


class ScriptStore(Protocol):
    async def save_script(self, metadata: ScriptMetadata, code: str) -> None: ...

    async def fetch_simulation_scripts(
        self, simulation_id: str
    ) -> List["StoredScript"]: ...

    async def fetch_user_scripts(self, user_id: str) -> List["StoredScript"]: ...

    async def list_all_metadata(self) -> List[ScriptMetadata]: ...

    async def update_simulation_binding(
        self, script_id: str, simulation_id: Optional[str]
    ) -> bool: ...

    async def delete_script(self, script_id: str) -> bool: ...

    async def delete_by_user(self, user_id: str) -> List[tuple[Optional[str], str]]: ...

    async def detach_simulation(self, simulation_id: str) -> List[str]: ...

    async def clear(self) -> None: ...

    async def update_failure_status(
        self,
        script_id: str,
        failure_at: Optional[datetime],
        failure_reason: Optional[str],
    ) -> None: ...


class SimulationLimitStore(Protocol):
    async def set_script_limit(self, simulation_id: str, limit: int) -> None: ...

    async def delete_script_limit(self, simulation_id: str) -> None: ...

    async def get_script_limit(self, simulation_id: str) -> Optional[int]: ...

    async def list_script_limits(self) -> Dict[str, int]: ...


class ScriptRegistry:
    """维护脚本与仿真实例之间关联关系的容器。"""

    PENDING_ENTITY_PREFIX = "pending:"

    def __init__(
        self,
        store: Optional[ScriptStore] = None,
        *,
        sandbox_timeout: float = DEFAULT_SANDBOX_TIMEOUT,
        max_scripts_per_user: Optional[int] = None,
        limit_store: Optional[SimulationLimitStore] = None,
    ) -> None:
        self._store = store
        self._records: Dict[str, _ScriptRecord] = {}
        self._simulation_index: Dict[str, Set[str]] = {}
        self._user_index: Dict[str, Set[str]] = {}
        self._entity_index: Dict[tuple[str, AgentKind, str], str] = {}
        self._loaded_simulations: Set[str] = set()
        self._loaded_users: Set[str] = set()
        self._load_lock = asyncio.Lock()
        self._registry_lock = asyncio.Lock()
        self._sandbox_timeout = sandbox_timeout
        self._allowed_modules = set(ALLOWED_MODULES)
        self._default_script_limit = self._normalize_limit(max_scripts_per_user)
        self._simulation_limits: Dict[str, int] = {}
        self._limit_missing: Set[str] = set()
        self._limit_store = limit_store

    @staticmethod
    def _normalize_limit(limit: Optional[int]) -> Optional[int]:
        if limit is None:
            return None
        if limit <= 0:
            return None
        return int(limit)

    def _get_effective_limit_unlocked(self, simulation_id: str) -> Optional[int]:
        if simulation_id in self._simulation_limits:
            return self._simulation_limits[simulation_id]
        return self._default_script_limit

    async def set_simulation_limit(
        self, simulation_id: str, limit: Optional[int]
    ) -> Optional[int]:
        normalized = self._normalize_limit(limit)
        async with self._registry_lock:
            if normalized is None:
                self._simulation_limits.pop(simulation_id, None)
                self._limit_missing.discard(simulation_id)
            else:
                self._simulation_limits[simulation_id] = normalized
                self._limit_missing.discard(simulation_id)
        if self._limit_store is not None:
            if normalized is None:
                await self._limit_store.delete_script_limit(simulation_id)
            else:
                await self._limit_store.set_script_limit(simulation_id, normalized)
        return normalized

    async def get_simulation_limit(self, simulation_id: str) -> Optional[int]:
        async with self._registry_lock:
            if simulation_id in self._simulation_limits:
                return self._simulation_limits[simulation_id]
            if simulation_id in self._limit_missing:
                return self._default_script_limit

        if self._limit_store is not None:
            stored = await self._limit_store.get_script_limit(simulation_id)
            async with self._registry_lock:
                if stored is not None:
                    self._simulation_limits[simulation_id] = stored
                    self._limit_missing.discard(simulation_id)
                    return stored
                self._limit_missing.add(simulation_id)
        return self._default_script_limit

    async def list_simulation_limits(self) -> Dict[str, Optional[int]]:
        if self._limit_store is None:
            async with self._registry_lock:
                return dict(self._simulation_limits)

        stored = await self._limit_store.list_script_limits()
        async with self._registry_lock:
            for simulation_id, limit in stored.items():
                self._simulation_limits[simulation_id] = limit
                self._limit_missing.discard(simulation_id)
        return {**stored}

    def get_default_limit(self) -> Optional[int]:
        return self._default_script_limit

    def _update_indexes(
        self,
        script_id: str,
        old_meta: Optional[ScriptMetadata],
        new_meta: Optional[ScriptMetadata],
    ) -> None:
        if old_meta is not None:
            user_bucket = self._user_index.get(old_meta.user_id)
            if user_bucket is not None:
                user_bucket.discard(script_id)
                if not user_bucket:
                    self._user_index.pop(old_meta.user_id, None)
            if old_meta.simulation_id:
                sim_bucket = self._simulation_index.get(old_meta.simulation_id)
                if sim_bucket is not None:
                    sim_bucket.discard(script_id)
                    if not sim_bucket:
                        self._simulation_index.pop(old_meta.simulation_id, None)
                entity_key = (
                    old_meta.simulation_id,
                    old_meta.agent_kind,
                    str(old_meta.entity_id),
                )
                bound = self._entity_index.get(entity_key)
                if bound == script_id:
                    self._entity_index.pop(entity_key, None)

        if new_meta is not None:
            self._user_index.setdefault(new_meta.user_id, set()).add(script_id)
            if new_meta.simulation_id:
                self._simulation_index.setdefault(new_meta.simulation_id, set()).add(
                    script_id
                )
                entity_key = (
                    new_meta.simulation_id,
                    new_meta.agent_kind,
                    str(new_meta.entity_id),
                )
                self._entity_index[entity_key] = script_id

    async def _ingest_stored_scripts(
        self, stored_scripts: Iterable["StoredScript"]
    ) -> None:
        prepared: List["StoredScript"] = []
        for stored in stored_scripts:
            script_id = stored.metadata.script_id
            record = self._records.get(script_id)
            if (
                record is not None
                and record.metadata.code_version == stored.metadata.code_version
            ):
                prepared.append(stored)
                continue

            try:
                self._validate_script(stored.code)
            except ScriptExecutionError as exc:
                logger.warning(
                    "Skip persisted script %s: %s",
                    stored.metadata.script_id,
                    exc,
                )
                continue
            prepared.append(stored)

        if not prepared:
            return

        async with self._registry_lock:
            for stored in prepared:
                script_id = stored.metadata.script_id
                existing = self._records.get(script_id)
                old_meta = existing.metadata if existing is not None else None
                self._records[script_id] = _ScriptRecord(
                    metadata=stored.metadata,
                    code=stored.code,
                )
                self._update_indexes(script_id, old_meta, stored.metadata)
                self._loaded_users.add(stored.metadata.user_id)

    async def _ensure_simulation_loaded(self, simulation_id: str) -> None:
        if simulation_id in self._loaded_simulations:
            return
        if self._store is None:
            async with self._registry_lock:
                self._simulation_index.setdefault(simulation_id, set())
                self._loaded_simulations.add(simulation_id)
            return

        async with self._load_lock:
            if simulation_id in self._loaded_simulations:
                return
            try:
                stored_scripts = await self._store.fetch_simulation_scripts(
                    simulation_id
                )
            except Exception as exc:  # pragma: no cover - defensive log
                logger.error(
                    "Failed to load scripts for simulation %s",
                    simulation_id,
                    exc_info=exc,
                )
                stored_scripts = []
            await self._ingest_stored_scripts(stored_scripts)
            async with self._registry_lock:
                self._simulation_index.setdefault(simulation_id, set())
                self._loaded_simulations.add(simulation_id)

    async def _ensure_user_loaded(self, user_id: str) -> None:
        if user_id in self._loaded_users:
            return
        if self._store is None:
            async with self._registry_lock:
                self._user_index.setdefault(user_id, set())
                self._loaded_users.add(user_id)
            return

        async with self._load_lock:
            if user_id in self._loaded_users:
                return
            try:
                stored_scripts = await self._store.fetch_user_scripts(user_id)
            except Exception as exc:  # pragma: no cover - defensive log
                logger.error(
                    "Failed to load scripts for user %s",
                    user_id,
                    exc_info=exc,
                )
                stored_scripts = []
            await self._ingest_stored_scripts(stored_scripts)
            async with self._registry_lock:
                self._user_index.setdefault(user_id, set())
                self._loaded_users.add(user_id)

    def _count_user_scripts_unlocked(self, simulation_id: str, user_id: str) -> int:
        script_ids = self._simulation_index.get(simulation_id, set())
        return sum(
            1
            for script_id in script_ids
            if script_id in self._records
            and self._records[script_id].metadata.user_id == user_id
        )

    async def _count_user_scripts(self, simulation_id: str, user_id: str) -> int:
        await self._ensure_simulation_loaded(simulation_id)
        await self._ensure_user_loaded(user_id)
        async with self._registry_lock:
            return self._count_user_scripts_unlocked(simulation_id, user_id)

    async def _enforce_script_limit(self, simulation_id: str, user_id: str) -> None:
        limit = await self.get_simulation_limit(simulation_id)
        if limit is None:
            return
        count = await self._count_user_scripts(simulation_id, user_id)
        if count >= limit:
            raise ScriptExecutionError(
                self._format_limit_message(simulation_id, user_id, limit)
            )

    def _format_limit_message(
        self, simulation_id: str, user_id: str, limit: int
    ) -> str:
        return (
            "达到脚本数量上限：用户 "
            f"{user_id} 在仿真实例 {simulation_id} 中最多允许 {limit} 个脚本"
        )

    @staticmethod
    def _normalize_entity_id(entity_id: str) -> str:
        normalized = str(entity_id).strip()
        if not normalized:
            raise ScriptExecutionError("entity_id must not be empty")
        return normalized

    @classmethod
    def _generate_placeholder_entity_id(cls, agent_kind: AgentKind) -> str:
        return f"{cls.PENDING_ENTITY_PREFIX}{agent_kind.value}:{uuid.uuid4().hex}"

    @classmethod
    def is_placeholder_entity_id(cls, entity_id: Optional[str]) -> bool:
        return isinstance(entity_id, str) and entity_id.startswith(
            cls.PENDING_ENTITY_PREFIX
        )

    @classmethod
    def _validate_entity_binding(cls, agent_kind: AgentKind, entity_id: str) -> None:
        if cls.is_placeholder_entity_id(entity_id):
            raise ScriptExecutionError("entity_id must be finalized before binding")
        if agent_kind is AgentKind.HOUSEHOLD and not str(entity_id).isdigit():
            raise ScriptExecutionError("Household 脚本必须使用纯数字的 entity_id")

    def _ensure_entity_available_unlocked(
        self,
        simulation_id: str,
        agent_kind: AgentKind,
        entity_id: str,
        *,
        ignore_script_id: Optional[str] = None,
    ) -> None:
        key = (simulation_id, agent_kind, entity_id)
        existing = self._entity_index.get(key)
        if existing is not None and existing != ignore_script_id:
            raise ScriptExecutionError(
                "指定实体已绑定其他脚本："
                f"simulation={simulation_id}, agent_kind={agent_kind.value}, entity_id={entity_id}"
            )
        if agent_kind is not AgentKind.HOUSEHOLD:
            bound_scripts = self._simulation_index.get(simulation_id, set())
            for script_id in bound_scripts:
                if script_id == ignore_script_id:
                    continue
                record = self._records.get(script_id)
                if record is None:
                    continue
                if record.metadata.agent_kind is agent_kind:
                    raise ScriptExecutionError(
                        "仿真实例当前仅支持一个该类型的主体脚本。"
                    )

    async def register_script(
        self,
        simulation_id: Optional[str],
        user_id: str,
        script_code: str,
        description: Optional[str] = None,
        *,
        agent_kind: AgentKind,
        entity_id: Optional[str] = None,
    ) -> ScriptMetadata:
        """编译并注册脚本，使其在后续 Tick 中参与决策。"""

        if entity_id is None:
            entity_id = self._generate_placeholder_entity_id(agent_kind)

        entity_id = self._normalize_entity_id(entity_id)
        self._validate_script(script_code)

        if simulation_id is not None:
            self._validate_entity_binding(agent_kind, entity_id)
            await self._enforce_script_limit(simulation_id, user_id)

        metadata = ScriptMetadata(
            script_id=str(uuid.uuid4()),
            simulation_id=simulation_id,
            user_id=user_id,
            description=description,
            created_at=datetime.now(timezone.utc),
            code_version=str(uuid.uuid4()),
            agent_kind=agent_kind,
            entity_id=entity_id,
        )

        if self._store is not None:
            await self._store.save_script(metadata, script_code)

        limit_violation: Optional[str] = None
        async with self._registry_lock:
            if simulation_id:
                limit = self._get_effective_limit_unlocked(simulation_id)
                if (
                    limit is not None
                    and self._count_user_scripts_unlocked(simulation_id, user_id)
                    >= limit
                ):
                    limit_violation = self._format_limit_message(
                        simulation_id, user_id, limit
                    )
                else:
                    self._ensure_entity_available_unlocked(
                        simulation_id, agent_kind, entity_id
                    )
                    self._records[metadata.script_id] = _ScriptRecord(
                        metadata=metadata,
                        code=script_code,
                    )
                    self._update_indexes(metadata.script_id, None, metadata)
                    self._loaded_users.add(user_id)
                    self._loaded_simulations.add(simulation_id)
            else:
                self._records[metadata.script_id] = _ScriptRecord(
                    metadata=metadata,
                    code=script_code,
                )
                self._update_indexes(metadata.script_id, None, metadata)
                self._loaded_users.add(user_id)

        if limit_violation is not None:
            if self._store is not None:
                try:
                    await self._store.delete_script(metadata.script_id)
                except Exception as exc:  # pragma: no cover - defensive log
                    logger.error(
                        "Rollback persisted script %s failed",
                        metadata.script_id,
                        exc_info=exc,
                    )
            raise ScriptExecutionError(limit_violation)

        return metadata

    async def list_scripts(self, simulation_id: str) -> List[ScriptMetadata]:
        """列出指定仿真实例下已注册的脚本。"""

        await self._ensure_simulation_loaded(simulation_id)
        async with self._registry_lock:
            script_ids = list(self._simulation_index.get(simulation_id, set()))
            records = [
                self._records[script_id]
                for script_id in script_ids
                if script_id in self._records
            ]
        return sorted(
            (record.metadata for record in records),
            key=lambda meta: meta.created_at,
        )

    async def list_user_scripts(self, user_id: str) -> List[ScriptMetadata]:
        """返回指定用户上传的所有脚本（包含未挂载仿真）。"""

        await self._ensure_user_loaded(user_id)
        async with self._registry_lock:
            script_ids = list(self._user_index.get(user_id, set()))
            records = [
                self._records[script_id]
                for script_id in script_ids
                if script_id in self._records
            ]
        return sorted(
            (record.metadata for record in records),
            key=lambda meta: meta.created_at,
        )

    async def list_all_scripts(self) -> List[ScriptMetadata]:
        """返回所有脚本的元数据。"""

        if self._store is not None:
            try:
                return await self._store.list_all_metadata()
            except Exception as exc:  # pragma: no cover - defensive log
                logger.error(
                    "Failed to list scripts from persistent store", exc_info=exc
                )

        async with self._registry_lock:
            scripts = [record.metadata for record in self._records.values()]
        scripts.sort(key=lambda meta: meta.created_at)
        return scripts

    async def attach_script(
        self,
        script_id: str,
        simulation_id: str,
        user_id: str,
        *,
        entity_id: Optional[str] = None,
    ) -> ScriptMetadata:
        """挂载已上传的脚本到指定仿真实例。"""

        await self._ensure_user_loaded(user_id)
        await self._ensure_simulation_loaded(simulation_id)

        async with self._registry_lock:
            record = self._records.get(script_id)
            if record is None or record.metadata.user_id != user_id:
                raise ScriptExecutionError("脚本不存在或无权限操作。")
            if record.metadata.simulation_id == simulation_id:
                return record.metadata

            limit = self._get_effective_limit_unlocked(simulation_id)
            if (
                limit is not None
                and self._count_user_scripts_unlocked(simulation_id, user_id) >= limit
            ):
                raise ScriptExecutionError(
                    self._format_limit_message(simulation_id, user_id, limit)
                )

        candidate_entity_id = entity_id or record.metadata.entity_id
        candidate_entity_id = self._normalize_entity_id(candidate_entity_id)
        self._validate_entity_binding(record.metadata.agent_kind, candidate_entity_id)

        self._ensure_entity_available_unlocked(
            simulation_id,
            record.metadata.agent_kind,
            candidate_entity_id,
            ignore_script_id=script_id,
        )

        if self._store is not None:
            try:
                updated = await self._store.update_simulation_binding(
                    script_id, simulation_id
                )
            except Exception as exc:  # pragma: no cover - defensive log
                raise ScriptExecutionError(f"无法挂载脚本: {exc}") from exc
            if not updated:
                raise ScriptExecutionError("脚本不存在或已被移除。")

        async with self._registry_lock:
            record = self._records.get(script_id)
            if record is None or record.metadata.user_id != user_id:
                raise ScriptExecutionError("脚本不存在或无权限操作。")
            old_metadata = record.metadata
            new_metadata = record.metadata.model_copy(
                update={
                    "simulation_id": simulation_id,
                    "entity_id": candidate_entity_id,
                }
            )
            record.metadata = new_metadata
            self._update_indexes(script_id, old_metadata, new_metadata)
            self._loaded_simulations.add(simulation_id)
            code_snapshot = record.code

        if self._store is not None:
            await self._store.save_script(new_metadata, code_snapshot)

        return new_metadata

    async def get_user_script(self, script_id: str, user_id: str) -> ScriptMetadata:
        """返回指定用户拥有的脚本元数据，若无权限则抛出异常。"""

        await self._ensure_user_loaded(user_id)
        async with self._registry_lock:
            record = self._records.get(script_id)
            if record is None or record.metadata.user_id != user_id:
                raise ScriptExecutionError("脚本不存在或无权限操作。")
            return record.metadata

    async def detach_user_script(self, script_id: str, user_id: str) -> ScriptMetadata:
        """将用户脚本从当前仿真实例中取消挂载。"""

        await self._ensure_user_loaded(user_id)
        placeholder_update = False
        code_snapshot: Optional[str] = None
        new_metadata: Optional[ScriptMetadata] = None
        async with self._registry_lock:
            record = self._records.get(script_id)
            if record is None or record.metadata.user_id != user_id:
                raise ScriptExecutionError("脚本不存在或无权限操作。")
            if record.metadata.simulation_id is None:
                if self.is_placeholder_entity_id(record.metadata.entity_id):
                    return record.metadata
                placeholder = self._generate_placeholder_entity_id(
                    record.metadata.agent_kind
                )
                new_metadata = record.metadata.model_copy(
                    update={"entity_id": placeholder}
                )
                record.metadata = new_metadata
                code_snapshot = record.code
                placeholder_update = True

        if placeholder_update and new_metadata is not None:
            if self._store is not None and code_snapshot is not None:
                await self._store.save_script(new_metadata, code_snapshot)
            return new_metadata

        if self._store is not None:
            try:
                updated = await self._store.update_simulation_binding(script_id, None)
            except Exception as exc:  # pragma: no cover - defensive log
                raise ScriptExecutionError(f"无法取消挂载脚本: {exc}") from exc
            if not updated:
                raise ScriptExecutionError("脚本不存在或已被移除。")

        async with self._registry_lock:
            record = self._records.get(script_id)
            if record is None:
                raise ScriptExecutionError("脚本不存在或已被移除。")
            old_metadata = record.metadata
            placeholder = self._generate_placeholder_entity_id(
                record.metadata.agent_kind
            )
            new_metadata = record.metadata.model_copy(
                update={"simulation_id": None, "entity_id": placeholder}
            )
            record.metadata = new_metadata
            self._update_indexes(script_id, old_metadata, new_metadata)
            code_snapshot = record.code

        if self._store is not None:
            await self._store.save_script(new_metadata, code_snapshot)
        return new_metadata

    async def remove_script(self, simulation_id: str, script_id: str) -> None:
        """根据脚本 ID 删除已注册脚本。"""

        await self._ensure_simulation_loaded(simulation_id)
        async with self._registry_lock:
            record = self._records.get(script_id)
            if record is None or record.metadata.simulation_id != simulation_id:
                raise ScriptExecutionError("Script not found for simulation")

        if self._store is not None:
            try:
                deleted = await self._store.delete_script(script_id)
            except Exception as exc:  # pragma: no cover - defensive log
                raise ScriptExecutionError(f"Failed to delete script: {exc}") from exc
            if not deleted:
                raise ScriptExecutionError("Script not found for simulation")

        async with self._registry_lock:
            record = self._records.pop(script_id, None)
            if record is not None:
                self._update_indexes(script_id, record.metadata, None)

    async def remove_scripts_by_user(self, user_id: str) -> int:
        """批量移除指定用户的所有脚本，返回删除数量。"""

        await self._ensure_user_loaded(user_id)
        async with self._registry_lock:
            script_ids = list(self._user_index.get(user_id, set()))
            removed = 0
            for script_id in script_ids:
                record = self._records.pop(script_id, None)
                if record is None:
                    continue
                self._update_indexes(script_id, record.metadata, None)
                removed += 1

        store_removed: List[tuple[Optional[str], str]] = []
        if self._store is not None:
            try:
                store_removed = await self._store.delete_by_user(user_id)
            except Exception as exc:  # pragma: no cover - defensive log
                logger.error(
                    "Failed to delete scripts for user %s in persistent store",
                    user_id,
                    exc_info=exc,
                )
            removed = max(removed, len(store_removed))
        return removed

    async def delete_user_script(self, script_id: str, user_id: str) -> bool:
        """删除用户拥有的脚本，不论是否挂载。"""

        await self._ensure_user_loaded(user_id)
        async with self._registry_lock:
            record = self._records.get(script_id)
            if record is None or record.metadata.user_id != user_id:
                raise ScriptExecutionError("脚本不存在或无权限操作。")

        if self._store is not None:
            try:
                deleted = await self._store.delete_script(script_id)
            except Exception as exc:  # pragma: no cover - defensive log
                raise ScriptExecutionError(f"删除脚本失败: {exc}") from exc
            if not deleted:
                raise ScriptExecutionError("脚本不存在或已被移除。")

        async with self._registry_lock:
            record = self._records.pop(script_id, None)
            if record is not None:
                self._update_indexes(script_id, record.metadata, None)
        return True

    async def delete_script_by_id(self, script_id: str) -> bool:
        """无论挂载状态如何，彻底删除指定脚本。"""

        store_deleted = False
        if self._store is not None:
            try:
                store_deleted = await self._store.delete_script(script_id)
            except Exception as exc:  # pragma: no cover - defensive log
                raise ScriptExecutionError(f"删除脚本失败: {exc}") from exc

        async with self._registry_lock:
            record = self._records.pop(script_id, None)
            if record is not None:
                self._update_indexes(script_id, record.metadata, None)
                store_deleted = True

        if not store_deleted:
            raise ScriptExecutionError("Script not found")
        return True

    async def update_script_code(
        self,
        *,
        script_id: str,
        user_id: Optional[str] = None,
        new_code: str,
        new_description: Optional[str] = None,
    ) -> ScriptMetadata:
        """更新现有脚本的代码并提升版本号。

        - 保持 script_id、simulation_id、agent_kind、entity_id 不变，用于“日终轮换”继承实体状态。
        - 可选地更新描述。
        - 验证新脚本合法性；持久化存储存在时同步写入。
        - 若提供 user_id，将校验拥有者；未提供则不校验（供管理员或内部流程使用）。
        """

        self._validate_script(new_code)

        async with self._registry_lock:
            record = self._records.get(script_id)
            if record is None:
                raise ScriptExecutionError("Script not found")
            if user_id is not None and record.metadata.user_id != user_id:
                raise ScriptExecutionError("脚本不存在或无权限操作。")

            old_meta = record.metadata
            updated_meta = old_meta.model_copy(
                update={
                    "code_version": str(uuid.uuid4()),
                    "description": new_description
                    if new_description is not None
                    else old_meta.description,
                }
            )
            record.metadata = updated_meta
            record.code = new_code

        if self._store is not None:
            try:
                await self._store.save_script(updated_meta, new_code)
            except Exception as exc:  # pragma: no cover - defensive log
                # 回滚内存更新以保持一致性
                async with self._registry_lock:
                    existing = self._records.get(script_id)
                    if existing is not None:
                        existing.metadata = old_meta
                        existing.code = record.code  # type: ignore[attr-defined]
                raise ScriptExecutionError(f"更新脚本失败: {exc}") from exc

        return updated_meta

    async def clear(self) -> None:
        """清空所有已注册脚本，主要用于测试。"""

        async with self._registry_lock:
            self._records.clear()
            self._simulation_index.clear()
            self._user_index.clear()
            self._loaded_simulations.clear()
            self._loaded_users.clear()
            self._simulation_limits.clear()
            self._limit_missing.clear()
        if self._store is not None:
            try:
                await self._store.clear()
            except Exception:  # pragma: no cover - best effort
                logger.exception("Failed to clear script store")
        if self._limit_store is not None:
            clear_method = getattr(self._limit_store, "clear", None)
            if callable(clear_method):
                try:
                    await clear_method()
                except Exception:  # pragma: no cover - defensive log
                    logger.exception("Failed to clear simulation limit store")

    async def detach_simulation(self, simulation_id: str) -> int:
        """移除与指定仿真实例关联的所有脚本，返回解除数量。"""

        await self._ensure_simulation_loaded(simulation_id)

        store_script_ids: List[str] = []
        if self._store is not None:
            try:
                store_script_ids = await self._store.detach_simulation(simulation_id)
            except Exception as exc:  # pragma: no cover - defensive log
                logger.error(
                    "Failed to detach scripts for simulation %s in persistent store",
                    simulation_id,
                    exc_info=exc,
                )

        async with self._registry_lock:
            script_ids = set(self._simulation_index.get(simulation_id, set()))
            script_ids.update(store_script_ids)
            if not script_ids:
                self._simulation_index.pop(simulation_id, None)
                self._loaded_simulations.discard(simulation_id)
                self._simulation_limits.pop(simulation_id, None)
                self._limit_missing.discard(simulation_id)
                return 0

            detached = 0
            for script_id in script_ids:
                record = self._records.get(script_id)
                if record is None:
                    continue
                old_metadata = record.metadata
                new_metadata = record.metadata.model_copy(
                    update={"simulation_id": None}
                )
                record.metadata = new_metadata
                self._update_indexes(script_id, old_metadata, new_metadata)
                detached += 1

            self._simulation_index.pop(simulation_id, None)
            self._loaded_simulations.discard(simulation_id)
            self._simulation_limits.pop(simulation_id, None)
            self._limit_missing.discard(simulation_id)
        if self._limit_store is not None:
            try:
                await self._limit_store.delete_script_limit(simulation_id)
            except Exception:  # pragma: no cover - defensive log
                logger.exception(
                    "Failed to delete script limit for simulation %s",
                    simulation_id,
                )
        return detached

    async def generate_overrides(
        self,
        simulation_id: str,
        world_state: WorldState,
        config: WorldConfig,
    ) -> tuple[
        Optional[TickDecisionOverrides],
        List[TickLogEntry],
        List[ScriptFailureEvent],
    ]:
        """依次执行所有脚本，并合并生成的决策覆盖与失败日志。"""

        await self._ensure_simulation_loaded(simulation_id)
        async with self._registry_lock:
            script_ids = list(self._simulation_index.get(simulation_id, set()))
            records = [
                self._records[script_id]
                for script_id in script_ids
                if script_id in self._records
            ]

        if not records:
            return None, [], []

        records.sort(key=lambda record: record.metadata.created_at)

        combined: Optional[TickDecisionOverrides] = None
        failure_logs: List[TickLogEntry] = []
        failure_events: List[ScriptFailureEvent] = []
        status_updates: List[tuple[str, Optional[datetime], Optional[str]]] = []

        for record in records:
            try:
                overrides = self._execute_script(record, world_state, config)
            except ScriptExecutionError as exc:
                logger.error(
                    "Script %s failed during tick %s: %s",
                    record.metadata.script_id,
                    world_state.tick,
                    exc,
                )
                timestamp = datetime.now(timezone.utc)
                failure_trace = traceback.format_exc()
                failure_logs.append(
                    TickLogEntry(
                        tick=world_state.tick,
                        day=world_state.day,
                        message=(f"脚本执行失败: {record.metadata.script_id}"),
                        context={
                            "agent_kind": record.metadata.agent_kind.value,
                            "entity_id": record.metadata.entity_id,
                            "script_id": record.metadata.script_id,
                            "user_id": record.metadata.user_id,
                        },
                    )
                )
                failure_events.append(
                    ScriptFailureEvent(
                        script_id=record.metadata.script_id,
                        simulation_id=simulation_id,
                        user_id=record.metadata.user_id,
                        agent_kind=record.metadata.agent_kind,
                        entity_id=record.metadata.entity_id,
                        message=str(exc),
                        traceback=failure_trace,
                        occurred_at=timestamp,
                    )
                )
                status_updates.append(
                    (
                        record.metadata.script_id,
                        timestamp,
                        str(exc),
                    )
                )
                continue

            combined = merge_tick_overrides(combined, overrides)
            if (
                record.metadata.last_failure_at is not None
                or record.metadata.last_failure_reason is not None
            ):
                status_updates.append((record.metadata.script_id, None, None))

        if status_updates:
            async with self._registry_lock:
                for script_id, failure_at, failure_reason in status_updates:
                    record = self._records.get(script_id)
                    if record is None:
                        continue
                    updated_meta = record.metadata.model_copy(
                        update={
                            "last_failure_at": failure_at,
                            "last_failure_reason": failure_reason,
                        }
                    )
                    record.metadata = updated_meta
                    # entity/user indexes remain unchanged

        if status_updates and self._store is not None:
            for script_id, failure_at, failure_reason in status_updates:
                try:
                    await self._store.update_failure_status(
                        script_id, failure_at, failure_reason
                    )
                except Exception:  # pragma: no cover - best effort persistence
                    logger.exception(
                        "Failed to persist failure status for script %s",
                        script_id,
                    )

        return combined, failure_logs, failure_events

    def _serialize_entity_state(
        self,
        metadata: ScriptMetadata,
        world_state: WorldState,
    ) -> Optional[dict[str, object]]:
        kind = metadata.agent_kind
        if kind is AgentKind.HOUSEHOLD:
            try:
                household_id = int(metadata.entity_id)
            except ValueError:
                return None
            entity = world_state.households.get(household_id)
            if entity is None:
                return None
            return entity.model_dump(mode="json")
        if kind is AgentKind.FIRM:
            entity = world_state.firm
            if entity is None or entity.id != metadata.entity_id:
                return None
            return entity.model_dump(mode="json")
        if kind is AgentKind.BANK:
            entity = world_state.bank
            if entity is None or entity.id != metadata.entity_id:
                return None
            return entity.model_dump(mode="json")
        if kind is AgentKind.GOVERNMENT:
            entity = world_state.government
            if entity is None or entity.id != metadata.entity_id:
                return None
            return entity.model_dump(mode="json")
        if kind is AgentKind.CENTRAL_BANK:
            entity = world_state.central_bank
            if entity is None or entity.id != metadata.entity_id:
                return None
            return entity.model_dump(mode="json")
        return None

    def _execute_script(
        self,
        record: _ScriptRecord,
        world_state: WorldState,
        config: WorldConfig,
    ) -> Optional[TickDecisionOverrides]:
        """调用脚本并解析返回的决策覆盖。"""

        entity_state = self._serialize_entity_state(record.metadata, world_state)
        context = {
            "world_state": world_state.model_dump(mode="json"),
            "config": config.model_dump(mode="json"),
            "script_api_version": 1,
            "agent_kind": record.metadata.agent_kind.value,
            "entity_id": record.metadata.entity_id,
            "entity_state": entity_state,
        }

        try:
            result = execute_script(
                record.code,
                context,
                timeout=self._sandbox_timeout,
                script_id=record.metadata.script_id,
                allowed_modules=self._allowed_modules,
            )
        except ScriptSandboxTimeout as exc:
            raise ScriptExecutionError(
                f"脚本执行超时: {record.metadata.script_id}"
            ) from exc
        except ScriptSandboxError as exc:
            raise ScriptExecutionError(
                f"脚本执行失败 ({record.metadata.script_id}): {exc}"
            ) from exc

        if result in (None, {}, []):
            return None

        try:
            return TickDecisionOverrides.model_validate(result)
        except ValidationError as exc:
            raise ScriptExecutionError(
                f"脚本返回结果解析失败 ({record.metadata.script_id}): {exc}"
            ) from exc

    def _validate_script(self, script_code: str) -> None:
        try:
            tree = ast.parse(script_code)
        except SyntaxError as exc:  # pragma: no cover - 语法检查
            raise ScriptExecutionError(f"脚本语法错误: {exc}") from exc

        has_entry = any(
            isinstance(node, ast.FunctionDef) and node.name == "generate_decisions"
            for node in tree.body
        )

        if not has_entry:
            raise ScriptExecutionError(
                "脚本中必须定义可调用的 generate_decisions(context) 函数"
            )

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not self._is_module_allowed(alias.name):
                        raise ScriptExecutionError(f"禁止导入模块: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    raise ScriptExecutionError("禁止使用相对导入")
                if not self._is_module_allowed(node.module):
                    raise ScriptExecutionError(f"禁止导入模块: {node.module}")

    def _is_module_allowed(self, module_name: str) -> bool:
        return any(
            module_name == allowed or module_name.startswith(f"{allowed}.")
            for allowed in self._allowed_modules
        )


__all__ = [
    "ScriptExecutionError",
    "ScriptMetadata",
    "ScriptRegistry",
    "ScriptStore",
    "ScriptFailureEvent",
]
