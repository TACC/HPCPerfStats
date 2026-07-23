"""Versioned persistence contract for sync_timedb operator sidecars."""
from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Callable, Dict, Optional, Tuple

from hpcperfstats.dbload.lib.print_utils import ingest_logging

# Bump when ANY persisted semantics change (day-close eligibility, checkpoint
# shape, manifest phase meaning, delete-gate assumptions, hints debt, etc.).
SYNC_TIMEDB_PERSISTENCE_CONTRACT_VERSION = 6

PERSISTENCE_CONTRACT_BASENAME = ".sync_timedb_persistence.json"

# Orphan sidecars removed from registry at v6; still deleted on contract reset.
LEGACY_ORPHAN_ARTIFACT_PATHS: Tuple[str, ...] = (
    ".sync_timedb_startup_tar_seal.json",
    ".sync_timedb_startup_raw_removal.json",
)

# Canonical artifact registry (kind -> relative path under archive_data_dir).
PERSISTENCE_ARTIFACT_REGISTRY: Dict[str, str] = {
    "ingest_checkpoint": ".sync_timedb_state.json",
    "archive_dead_letter": ".sync_timedb_dead_letter.json",
    "archive_maint_hints": ".sync_archive_maint_hints.json",
    "day_close_manifest": ".sync_timedb_async_day_close.json",
    "day_raw_removal_dir": ".sync_timedb_day_raw_removal",
    "unparsable_raw": ".sync_timedb_unparsable_raw.json",
    "zero_host_ingest_mark": ".sync_timedb_zero_host_ingest_mark.json",
}

INGEST_CHECKPOINT_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1
MAINT_HINTS_SCHEMA_VERSION = 2
UNPARSABLE_RAW_SCHEMA_VERSION = 1
DEAD_LETTER_SCHEMA_VERSION = 1
ZERO_HOST_INGEST_MARK_SCHEMA_VERSION = 1

LogFn = Optional[Callable[..., Any]]


def persistence_contract_path(archive_data_dir: str) -> str:
  return os.path.join(archive_data_dir, PERSISTENCE_CONTRACT_BASENAME)


def artifact_path(archive_data_dir: str, kind: str) -> str:
  rel = PERSISTENCE_ARTIFACT_REGISTRY.get(kind)
  if rel is None:
    raise KeyError("unknown persistence artifact kind: %s" % kind)
  return os.path.join(archive_data_dir, rel)


def _read_json_file(path: str) -> Any:
  try:
    with open(path, "r", encoding="utf-8") as handle:
      return json.load(handle)
  except (OSError, ValueError, TypeError, json.JSONDecodeError):
    return None


def _save_json_atomic(path: str, payload: Any, *, compact: bool = False) -> None:
  parent = os.path.dirname(str(path)) or "."
  os.makedirs(parent, exist_ok=True)
  fd, tmp_path = tempfile.mkstemp(
      prefix=".atomic.",
      suffix=os.path.basename(path),
      dir=parent,
  )
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
      if compact:
        json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
      else:
        json.dump(payload, handle)
    os.replace(tmp_path, path)
  except Exception:
    try:
      os.unlink(tmp_path)
    except OSError:
      pass
    raise


def _unlink_path(path: str, log_fn: LogFn) -> None:
  try:
    if os.path.isfile(path):
      os.remove(path)
  except OSError as exc:
    if log_fn:
      log_fn(
          "persistence reset could not unlink %s: %s"
          % (path, exc),
          flush=True,
      )


def _unlink_tree(path: str, log_fn: LogFn) -> None:
  if not os.path.isdir(path):
    _unlink_path(path, log_fn)
    return
  for root, dirs, files in os.walk(path, topdown=False):
    for name in files:
      _unlink_path(os.path.join(root, name), log_fn)
    for name in dirs:
      try:
        os.rmdir(os.path.join(root, name))
      except OSError as exc:
        if log_fn:
          log_fn(
              "persistence reset could not rmdir %s: %s"
              % (os.path.join(root, name), exc),
              flush=True,
          )
  try:
    os.rmdir(path)
  except OSError as exc:
    if log_fn:
      log_fn(
          "persistence reset could not rmdir %s: %s" % (path, exc),
          flush=True,
      )


def reset_sync_timedb_persistence(archive_data_dir: str, *, log_fn: LogFn = None) -> None:
  """Delete all registered sidecar artifacts (best-effort)."""
  if not archive_data_dir:
    return
  for kind, rel in PERSISTENCE_ARTIFACT_REGISTRY.items():
    path = os.path.join(archive_data_dir, rel)
    if kind == "day_raw_removal_dir":
      _unlink_tree(path, log_fn)
    else:
      _unlink_path(path, log_fn)
  for rel in LEGACY_ORPHAN_ARTIFACT_PATHS:
    _unlink_path(os.path.join(archive_data_dir, rel), log_fn)
  _unlink_path(persistence_contract_path(archive_data_dir), log_fn)


def _read_contract_version(archive_data_dir: str) -> Optional[int]:
  raw = _read_json_file(persistence_contract_path(archive_data_dir))
  if not isinstance(raw, dict):
    return None
  version = raw.get("contract_version")
  try:
    return int(version)
  except (TypeError, ValueError):
    return None


def ensure_persistence_contract(archive_data_dir: str, *, log_fn: LogFn = None) -> bool:
  """Ensure on-disk contract matches current version; reset sidecars when stale.

  Returns True when a reset ran (operators should expect full reprocess).
  """
  with ingest_logging():
    return _ensure_persistence_contract_inner(archive_data_dir, log_fn=log_fn)


def _ensure_persistence_contract_inner(
    archive_data_dir: str, *, log_fn: LogFn = None,
) -> bool:
  if not archive_data_dir:
    return False
  os.makedirs(archive_data_dir, exist_ok=True)
  current = SYNC_TIMEDB_PERSISTENCE_CONTRACT_VERSION
  on_disk = _read_contract_version(archive_data_dir)
  if on_disk == current:
    if log_fn:
      log_fn(
          "persistence contract v%d active" % current,
          flush=True,
      )
    return False
  reset_sync_timedb_persistence(archive_data_dir, log_fn=log_fn)
  payload = {
      "contract_version": current,
      "written_at": time.time(),
  }
  _save_json_atomic(persistence_contract_path(archive_data_dir), payload)
  if log_fn:
    log_fn(
        "persistence reset old=%s new=%d"
        % (on_disk if on_disk is not None else "missing", current),
        flush=True,
    )
  return True


def _expected_schema_version(kind: str) -> Optional[int]:
  if kind == "ingest_checkpoint":
    return INGEST_CHECKPOINT_SCHEMA_VERSION
  if kind == "archive_dead_letter":
    return DEAD_LETTER_SCHEMA_VERSION
  if kind == "unparsable_raw":
    return UNPARSABLE_RAW_SCHEMA_VERSION
  if kind == "archive_maint_hints":
    return MAINT_HINTS_SCHEMA_VERSION
  if kind in (
      "day_close_manifest",
      "day_raw_removal",
  ):
    return MANIFEST_SCHEMA_VERSION
  if kind == "zero_host_ingest_mark":
    return ZERO_HOST_INGEST_MARK_SCHEMA_VERSION
  return None


def _validate_envelope(raw: Any, *, kind: str, log_fn: LogFn = None) -> bool:
  """Return False when envelope schema/version/shape is unsupported."""
  if raw is None:
    return False
  expected = _expected_schema_version(kind)
  if kind in ("ingest_checkpoint", "archive_dead_letter", "unparsable_raw"):
    if isinstance(raw, list):
      return True
    if not isinstance(raw, dict):
      return False
    schema = raw.get("schema_version")
    if schema is not None and expected is not None:
      try:
        if int(schema) != expected:
          if log_fn:
            log_fn(
                "reject %s schema_version=%s expected=%s"
                % (kind, schema, expected),
                flush=True,
            )
          return False
      except (TypeError, ValueError):
        return False
    if not isinstance(raw.get("entries"), list):
      return False
    return True
  if kind == "archive_maint_hints":
    if not isinstance(raw, dict):
      return False
    schema = raw.get("schema_version", raw.get("version"))
    if schema is not None and expected is not None:
      try:
        if int(schema) != expected:
          if log_fn:
            log_fn(
                "reject archive_maint_hints schema_version=%s expected=%s"
                % (schema, expected),
                flush=True,
            )
          return False
      except (TypeError, ValueError):
        return False
    return True
  if kind in (
      "day_close_manifest",
      "day_raw_removal",
  ):
    if not isinstance(raw, dict):
      return False
    schema = raw.get("schema_version", raw.get("version"))
    if schema is not None and expected is not None:
      try:
        if int(schema) != expected:
          if log_fn:
            log_fn(
                "reject %s schema_version=%s expected=%s"
                % (kind, schema, expected),
                flush=True,
            )
          return False
      except (TypeError, ValueError):
        return False
    from hpcperfstats.dbload.lib.sync_timedb_manifest_contract import (
        validate_manifest_payload,
    )
    if not validate_manifest_payload(kind, raw):
      if log_fn:
        log_fn(
            "reject %s manifest missing required fields" % kind,
            flush=True,
        )
      return False
    return True
  if kind == "zero_host_ingest_mark":
    if not isinstance(raw, dict):
      return False
    schema = raw.get("schema_version")
    if schema is not None and expected is not None:
      try:
        if int(schema) != expected:
          if log_fn:
            log_fn(
                "reject %s schema_version=%s expected=%s"
                % (kind, schema, expected),
                flush=True,
            )
          return False
      except (TypeError, ValueError):
        return False
    entries = raw.get("entries")
    if entries is None:
      return True
    return isinstance(entries, dict)
  return True


def _unwrap_envelope(raw: Any, *, kind: str) -> Any:
  if kind == "ingest_checkpoint":
    if isinstance(raw, list):
      return raw
    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
      return raw["entries"]
    return None
  if kind == "archive_dead_letter":
    if isinstance(raw, list):
      return raw
    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
      return raw["entries"]
    return None
  if kind == "unparsable_raw":
    if isinstance(raw, list):
      return raw
    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
      return raw["entries"]
    return None
  if kind == "archive_maint_hints":
    if isinstance(raw, dict):
      return raw
    return None
  if kind in (
      "day_close_manifest",
  ):
    if isinstance(raw, dict):
      return raw
    return None
  if kind == "day_raw_removal":
    if isinstance(raw, dict):
      return raw
    return None
  if kind == "zero_host_ingest_mark":
    if isinstance(raw, dict):
      return raw
    return None
  return raw


def load_persistence_document(
    path: str,
    kind: str,
    *,
    default: Any = None,
    log_fn: LogFn = None,
) -> Any:
  """Load a registered artifact after ``ensure_persistence_contract``."""
  if default is None:
    if kind == "ingest_checkpoint":
      default = []
    elif kind in ("archive_dead_letter", "unparsable_raw"):
      default = []
    elif kind == "archive_maint_hints":
      default = None
    elif kind in (
        "day_close_manifest",
        "day_raw_removal",
    ):
      default = None
    elif kind == "zero_host_ingest_mark":
      default = {"entries": {}}
    else:
      default = None
  if not path or not os.path.isfile(path):
    return default
  raw = _read_json_file(path)
  if not _validate_envelope(raw, kind=kind, log_fn=log_fn):
    return default
  unwrapped = _unwrap_envelope(raw, kind=kind)
  if unwrapped is None:
    return default
  return unwrapped


def save_persistence_document(
    path: str,
    kind: str,
    payload: Any,
    *,
    compact: bool = True,
) -> None:
  """Persist a registered artifact with contract/schema envelope where needed."""
  contract_version = SYNC_TIMEDB_PERSISTENCE_CONTRACT_VERSION
  if kind == "ingest_checkpoint":
    envelope = {
        "contract_version": contract_version,
        "schema_version": INGEST_CHECKPOINT_SCHEMA_VERSION,
        "entries": list(payload or []),
    }
    _save_json_atomic(path, envelope, compact=compact)
    return
  if kind == "archive_dead_letter":
    envelope = {
        "contract_version": contract_version,
        "schema_version": DEAD_LETTER_SCHEMA_VERSION,
        "entries": list(payload or []),
    }
    _save_json_atomic(path, envelope, compact=compact)
    return
  if kind == "unparsable_raw":
    envelope = {
        "contract_version": contract_version,
        "schema_version": UNPARSABLE_RAW_SCHEMA_VERSION,
        "entries": list(payload or []),
    }
    _save_json_atomic(path, envelope, compact=compact)
    return
  if kind == "archive_maint_hints":
    if not isinstance(payload, dict):
      payload = {}
    payload = dict(payload)
    payload["contract_version"] = contract_version
    payload.setdefault("schema_version", MAINT_HINTS_SCHEMA_VERSION)
    payload.pop("version", None)
    _save_json_atomic(path, payload, compact=compact)
    return
  if kind in (
      "day_close_manifest",
      "day_raw_removal",
  ):
    if not isinstance(payload, dict):
      payload = {}
    payload = dict(payload)
    payload["contract_version"] = contract_version
    payload.setdefault("schema_version", MANIFEST_SCHEMA_VERSION)
    _save_json_atomic(path, payload, compact=compact)
    return
  if kind == "zero_host_ingest_mark":
    if not isinstance(payload, dict):
      payload = {}
    payload = dict(payload)
    payload["contract_version"] = contract_version
    payload.setdefault(
        "schema_version", ZERO_HOST_INGEST_MARK_SCHEMA_VERSION,
    )
    if not isinstance(payload.get("entries"), dict):
      payload["entries"] = {}
    _save_json_atomic(path, payload, compact=compact)
    return
  _save_json_atomic(path, payload, compact=compact)
