import copy
import json
import pytest

from econ_sim.data_access.redis_client import (
    CompositeStateStore,
    InMemoryStateStore,
    PersistenceError,
)


class RecordingStore:
    def __init__(
        self,
        *,
        initial=None,
        fail_on_store: bool = False,
        fail_on_load: bool = False,
        fail_on_delete: bool = False,
    ) -> None:
        self._data = initial or {}
        self.fail_on_store = fail_on_store
        self.fail_on_load = fail_on_load
        self.fail_on_delete = fail_on_delete
        self.store_calls = 0
        self.load_calls = 0
        self.delete_calls = 0

    async def load(self, simulation_id: str):
        self.load_calls += 1
        if self.fail_on_load:
            raise RuntimeError("load failure")
        value = self._data.get(simulation_id)
        return json.loads(json.dumps(value)) if value is not None else None

    async def store(self, simulation_id: str, payload):
        self.store_calls += 1
        if self.fail_on_store:
            raise RuntimeError("store failure")
        self._data[simulation_id] = copy.deepcopy(payload)

    async def delete(self, simulation_id: str):
        self.delete_calls += 1
        if self.fail_on_delete:
            raise RuntimeError("delete failure")
        self._data.pop(simulation_id, None)


@pytest.mark.asyncio
async def test_composite_store_prefers_cache_first():
    cache = RecordingStore(initial={"sim": {"tick": 1}}, fail_on_store=False)
    persistent = RecordingStore(initial={"sim": {"tick": 2}})
    store = CompositeStateStore(cache=cache, persistent=persistent)

    payload = await store.load("sim")

    assert payload == {"tick": 1}
    assert cache.load_calls == 1
    assert persistent.load_calls == 0


@pytest.mark.asyncio
async def test_composite_store_writes_through_to_persistent_and_cache():
    cache = RecordingStore()
    persistent = RecordingStore()
    store = CompositeStateStore(cache=cache, persistent=persistent)

    payload = {"tick": 3, "day": 1}
    await store.store("sim", payload)

    # Cache may fail silently; ensure persistent captured value
    assert persistent._data["sim"] == payload
    assert cache.store_calls == 1
    assert persistent.store_calls == 1


@pytest.mark.asyncio
async def test_composite_store_raises_if_persistent_write_fails():
    cache = RecordingStore()
    persistent = RecordingStore(fail_on_store=True)
    store = CompositeStateStore(cache=cache, persistent=persistent)

    with pytest.raises(PersistenceError):
        await store.store("sim", {"tick": 0})


@pytest.mark.asyncio
async def test_composite_store_falls_back_when_persistent_unavailable():
    cache = RecordingStore()
    persistent = RecordingStore(fail_on_load=True)
    fallback = InMemoryStateStore()
    await fallback.store("sim", {"tick": 7})

    store = CompositeStateStore(cache=cache, persistent=persistent, fallback=fallback)

    payload = await store.load("sim")

    assert payload == {"tick": 7}
    assert cache.store_calls == 1  # cache warmed from fallback
    assert persistent.load_calls == 1
