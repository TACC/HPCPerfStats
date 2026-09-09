"""N10: Manager-safe registry set/pop helpers (best-effort except-pass)."""

from __future__ import annotations

from hpcperfstats.dbload.lib import (
    sync_timedb_ingest_worker_diagnostics as diag,
)


class _ExplodingSet:
    """Mapping that fails item assign, then optional update."""

    def __init__(self, *, fail_update: bool = False) -> None:
        self.store: dict[str, object] = {}
        self.fail_update = fail_update
        self.assign_tries = 0
        self.update_tries = 0

    def __setitem__(self, key: str, value: object) -> None:
        self.assign_tries += 1
        raise RuntimeError("manager set")

    def update(self, mapping: dict[str, object]) -> None:
        self.update_tries += 1
        if self.fail_update:
            raise RuntimeError("manager update")
        self.store.update(mapping)

    def __contains__(self, key: object) -> bool:
        return key in self.store

    def __getitem__(self, key: str) -> object:
        return self.store[key]


class _ExplodingPop:
    def __init__(self) -> None:
        self.pop_tries = 0

    def pop(self, key: str, default: object = None) -> object:
        self.pop_tries += 1
        raise RuntimeError("manager pop")


def test_registry_set_success_item_assign():
    registry: dict[str, object] = {}
    diag._registry_set(registry, "1", {"stage": "ingest"})
    assert registry["1"] == {"stage": "ingest"}


def test_registry_set_falls_back_to_update():
    registry = _ExplodingSet()
    diag._registry_set(registry, "1", {"stage": "ingest"})
    assert registry.assign_tries == 1
    assert registry.update_tries == 1
    assert registry.store["1"] == {"stage": "ingest"}


def test_registry_set_swallows_update_failure():
    registry = _ExplodingSet(fail_update=True)
    diag._registry_set(registry, "1", {"stage": "ingest"})
    assert registry.assign_tries == 1
    assert registry.update_tries == 1
    assert registry.store == {}


def test_registry_pop_success():
    registry = {"1": {"stage": "x"}}
    diag._registry_pop(registry, "1")
    assert registry == {}


def test_registry_pop_swallows_manager_error():
    registry = _ExplodingPop()
    diag._registry_pop(registry, "1")
    assert registry.pop_tries == 1


def test_record_and_clear_use_helpers_and_never_raise(monkeypatch):
    exploding = _ExplodingSet(fail_update=True)
    monkeypatch.setattr(diag, "_resolve_registry", lambda: exploding)
    diag.record_worker_stage("/x", "ingest", timeout_s="bad")
    diag.clear_worker_stage()
    exploding2 = _ExplodingPop()
    monkeypatch.setattr(diag, "_resolve_registry", lambda: exploding2)
    diag.clear_worker_stage()
    assert exploding2.pop_tries == 1
