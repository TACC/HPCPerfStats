"""Regression tests for sync_timedb persistence contract API."""
from __future__ import annotations

import json
import os

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
    if kind == "day_raw_removal_dir":
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
    if kind == "day_raw_removal_dir":
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
def test_reset_sync_timedb_persistence_unlinks_registry_paths(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  _touch_artifacts(archive_dir)
  reset_sync_timedb_persistence(archive_dir)
  for kind, rel in PERSISTENCE_ARTIFACT_REGISTRY.items():
    path = os.path.join(archive_dir, rel)
    if kind == "day_raw_removal_dir":
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
