"""Regression tests for sync_timedb persistence contract API."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from hpcperfstats.dbload.lib import sync_timedb_persistence as persist_mod
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    PERSISTENCE_ARTIFACT_REGISTRY,
    SYNC_TIMEDB_PERSISTENCE_CONTRACT_VERSION,
    ensure_persistence_contract,
    load_persistence_document,
    reset_sync_timedb_persistence,
    save_persistence_document,
)


def _touch_artifacts(archive_dir: str) -> None:
  for kind, rel in PERSISTENCE_ARTIFACT_REGISTRY.items():
    path = os.path.join(archive_dir, rel)
    if kind in ("day_raw_removal_dir", "archive_members_store_dir"):
      os.makedirs(path, exist_ok=True)
      with open(os.path.join(path, "2020-01-01.json"), "w", encoding="utf-8") as handle:
        handle.write("{}")
    else:
      parent = os.path.dirname(path)
      if parent:
        os.makedirs(parent, exist_ok=True)
      with open(path, "w", encoding="utf-8") as handle:
        if kind == "ingest_checkpoint":
          json.dump([{"path": "/x", "size": 1, "mtime": 2}], handle)
        else:
          json.dump({"legacy": True}, handle)


@pytest.mark.django_db(databases=[])
def test_stale_contract_resets_all_registered_artifacts(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  _touch_artifacts(archive_dir)
  contract_path = persist_mod.persistence_contract_path(archive_dir)
  with open(contract_path, "w", encoding="utf-8") as handle:
    json.dump({"contract_version": 1, "written_at": 0.0}, handle)

  logs: list[str] = []
  reset_ran = ensure_persistence_contract(archive_dir, log_fn=lambda msg, **_kw: logs.append(str(msg)))
  assert reset_ran is True
  for kind, rel in PERSISTENCE_ARTIFACT_REGISTRY.items():
    path = os.path.join(archive_dir, rel)
    if kind in ("day_raw_removal_dir", "archive_members_store_dir"):
      assert not os.path.isdir(path)
    else:
      assert not os.path.isfile(path)
  with open(contract_path, encoding="utf-8") as handle:
    payload = json.load(handle)
  assert payload["contract_version"] == SYNC_TIMEDB_PERSISTENCE_CONTRACT_VERSION
  assert any("persistence reset" in line for line in logs)


@pytest.mark.django_db(databases=[])
def test_matching_contract_preserves_artifacts(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  checkpoint_path = os.path.join(archive_dir, PERSISTENCE_ARTIFACT_REGISTRY["ingest_checkpoint"])
  ensure_persistence_contract(archive_dir, log_fn=lambda *_a, **_kw: None)
  save_persistence_document(
      checkpoint_path,
      "ingest_checkpoint",
      [{"path": "/keep", "size": 9, "mtime": 8}],
  )
  loaded = load_persistence_document(checkpoint_path, "ingest_checkpoint", default=[])
  assert loaded == [{"path": "/keep", "size": 9, "mtime": 8}]


@pytest.mark.django_db(databases=[])
def test_ingest_checkpoint_envelope_round_trip(tmp_path):
  path = str(tmp_path / "state.json")
  entries = [{"path": "/a/b", "size": 10, "mtime": 20}]
  save_persistence_document(path, "ingest_checkpoint", entries)
  with open(path, encoding="utf-8") as handle:
    raw = json.load(handle)
  assert raw["contract_version"] == SYNC_TIMEDB_PERSISTENCE_CONTRACT_VERSION
  assert raw["entries"] == entries
  assert load_persistence_document(path, "ingest_checkpoint") == entries


@pytest.mark.django_db(databases=[])
def test_load_rejects_unsupported_schema_version(tmp_path):
  path = str(tmp_path / "state.json")
  with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "contract_version": SYNC_TIMEDB_PERSISTENCE_CONTRACT_VERSION,
            "schema_version": 999,
            "entries": [{"path": "/x", "size": 1, "mtime": 2}],
        },
        handle,
    )
  logs = []
  loaded = load_persistence_document(
      path,
      "ingest_checkpoint",
      log_fn=lambda msg, **_kw: logs.append(str(msg)),
  )
  assert loaded == []
  assert any("reject" in line for line in logs)


@pytest.mark.django_db(databases=[])
def test_reset_sync_timedb_persistence_unlinks_registry_paths(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  _touch_artifacts(archive_dir)
  reset_sync_timedb_persistence(archive_dir)
  for kind, rel in PERSISTENCE_ARTIFACT_REGISTRY.items():
    path = os.path.join(archive_dir, rel)
    if kind in ("day_raw_removal_dir", "archive_members_store_dir"):
      assert not os.path.exists(path)
    else:
      assert not os.path.isfile(path)


@pytest.mark.django_db(databases=[])
def test_persistence_artifact_registry_matches_dbload_sidecars():
  """Drift guard: registry keys cover canonical ``.sync_*`` sidecar basenames."""
  import pathlib

  dbload = pathlib.Path(persist_mod.__file__).resolve().parent
  basenames = set()
  for path in dbload.glob("*.py"):
    text = path.read_text(encoding="utf-8")
    for token in text.split('"'):
      if token.startswith(".sync_"):
        basenames.add(token)
  registry_paths = set(PERSISTENCE_ARTIFACT_REGISTRY.values())
  # Every registered file path must appear somewhere in dbload modules.
  for rel in registry_paths:
    assert rel in basenames or rel.rstrip("/") in {
        b.rstrip("/") for b in basenames
    }, "registry path missing from dbload sidecar references: %s" % rel


@pytest.mark.django_db(databases=[])
def test_save_json_atomic_concurrent_writers_no_enoent(tmp_path):
  """Parallel writers must not ENOENT on a shared fixed .tmp basename (RC maint hints)."""
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  path = persist_mod.artifact_path(archive_dir, "archive_maint_hints")
  errors: list[BaseException] = []

  def writer(worker_id: int) -> None:
    try:
      for iteration in range(40):
        save_persistence_document(
            path,
            "archive_maint_hints",
            {
                "host_dirs": {},
                "paths": {},
                "validated_days": {},
                "day_phases": {
                    "/daily/%s-%s.tar" % (worker_id, iteration): "sealed",
                },
                "debt_queue": [{
                    "kind": "DAY_CLOSE",
                    "tar_path": "/daily/%s-%s.tar" % (worker_id, iteration),
                }],
            },
            compact=True,
        )
    except BaseException as exc:
      errors.append(exc)

  with ThreadPoolExecutor(max_workers=8) as pool:
    futures = [pool.submit(writer, worker_id) for worker_id in range(8)]
    for future in as_completed(futures):
      future.result()

  assert errors == []
  assert os.path.isfile(path)
  with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
  assert payload.get("contract_version") == SYNC_TIMEDB_PERSISTENCE_CONTRACT_VERSION
  assert "schema_version" in payload
  assert "version" not in payload
  assert load_persistence_document(path, "archive_maint_hints") is not None


@pytest.mark.django_db(databases=[])
def test_reset_unlinks_legacy_orphan_artifacts(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  for rel in persist_mod.LEGACY_ORPHAN_ARTIFACT_PATHS:
    path = os.path.join(archive_dir, rel)
    with open(path, "w", encoding="utf-8") as handle:
      json.dump({"phase": "legacy"}, handle)
  reset_sync_timedb_persistence(archive_dir, log_fn=lambda *_a, **_kw: None)
  for rel in persist_mod.LEGACY_ORPHAN_ARTIFACT_PATHS:
    assert not os.path.isfile(os.path.join(archive_dir, rel))


@pytest.mark.django_db(databases=[])
def test_persistence_contract_bump_clears_zero_host_marks(tmp_path):
  """Version mismatch at startup must unlink poisoned zero-host marks (RC-0)."""
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  mark_rel = PERSISTENCE_ARTIFACT_REGISTRY["zero_host_ingest_mark"]
  mark_path = os.path.join(archive_dir, mark_rel)
  with open(mark_path, "w", encoding="utf-8") as handle:
    json.dump({"entries": [{"path": "/poisoned", "fp": "x"}]}, handle)
  contract_path = persist_mod.persistence_contract_path(archive_dir)
  with open(contract_path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "contract_version": SYNC_TIMEDB_PERSISTENCE_CONTRACT_VERSION - 1,
            "written_at": 0.0,
        },
        handle,
    )
  reset_ran = ensure_persistence_contract(
      archive_dir, log_fn=lambda *_a, **_kw: None
  )
  assert reset_ran is True
  assert not os.path.isfile(mark_path)
  with open(contract_path, encoding="utf-8") as handle:
    payload = json.load(handle)
  assert payload["contract_version"] == SYNC_TIMEDB_PERSISTENCE_CONTRACT_VERSION
