import json
from typing import Dict

import pytest

from econ_sim.data_access.redis_client import (
    CompositeStateStore,
    DataAccessLayer,
    InMemoryStateStore,
)
from econ_sim.utils.settings import get_world_config


class FakePersistentStore:
    def __init__(self, data: Dict[str, Dict]):
        self._data = {key: json.loads(json.dumps(value)) for key, value in data.items()}
        self.list_calls = 0

    async def load(self, simulation_id: str):  # pragma: no cover - not used in test
        stored = self._data.get(simulation_id)
        return json.loads(json.dumps(stored)) if stored is not None else None

    async def store(self, simulation_id: str, payload: Dict):  # pragma: no cover
        self._data[simulation_id] = json.loads(json.dumps(payload))

    async def delete(self, simulation_id: str):  # pragma: no cover
        self._data.pop(simulation_id, None)

    async def list_simulation_ids(self):
        self.list_calls += 1
        return sorted(self._data.keys())


@pytest.mark.asyncio
async def test_data_access_hydrates_simulations_from_persistent_store():
    persistent = FakePersistentStore(
        {
            "sim-alpha": {"tick": 1, "day": 0},
            "sim-beta": {"tick": 2, "day": 1},
        }
    )
    fallback = InMemoryStateStore()
    composite = CompositeStateStore(persistent=persistent, fallback=fallback)
    data_access = DataAccessLayer(
        config=get_world_config(),
        store=composite,
        persistent_store=persistent,
        fallback_store=fallback,
    )

    assert data_access._known_simulations == set()

    result = await data_access.list_simulations()

    assert result == ["sim-alpha", "sim-beta"]
    assert data_access._known_simulations == {"sim-alpha", "sim-beta"}
    assert persistent.list_calls == 1

    # Subsequent calls should not re-query once hydrated
    result_again = await data_access.list_simulations()
    assert result_again == ["sim-alpha", "sim-beta"]
    assert persistent.list_calls == 1
