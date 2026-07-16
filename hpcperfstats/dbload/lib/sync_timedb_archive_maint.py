"""Archive maintenance snapshot, hints, and parallel head metadata discovery.

Path hints in ``.sync_archive_maint_hints.json`` are a performance cache keyed on
``(mtime, size)`` per raw stats file (see ``_path_fingerprint``). In-place content
changes that leave metadata unchanged are not detected; correctness is restored
when mtime/size drift, the host-dir fingerprint changes, or head metadata is
re-read on a cache miss.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set, Tuple

from hpcperfstats.dbload.lib.sync_timedb_session_executor import (
    iter_bounded_thread_pool,
)

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.archive_compress import compressed_sibling_paths
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    build_archive_mapping,
    collect_stats_files_in_range,
    detect_compressed_format,
    normalize_daily_compressed_path,
    read_stats_file_head_identity,
    read_stats_file_tail_identity,
)
from hpcperfstats.dbload.lib.print_utils import log_print

SYNC_ARCHIVE_MAINT_HINTS_BASENAME = ".sync_archive_maint_hints.json"
_MAINT_HINTS_VERSION = 2
_ARCHIVE_METADATA_PROGRESS_MIN_PATHS = 1000
_ARCHIVE_METADATA_PROGRESS_EVERY_N = 5000
_ARCHIVE_METADATA_PROGRESS_INTERVAL_S = 60.0


@dataclass
class ArchiveMaintenanceSnapshot:
  """One discovery pass worth of archive maintenance inputs."""

  closed_paths: list
  first_timestamp_by_path: Dict[str, str] = field(default_factory=dict)
  head_identity_by_path: Dict[str, Tuple[str, int]] = field(default_factory=dict)
  gate_identities_by_path: Dict[str, Dict[str, Set[int]]] = field(
      default_factory=dict,
  )
  mapping: Dict[str, list] = field(default_factory=dict)
  remaining_raw_by_gz: Dict[str, list] = field(default_factory=dict)
  ready_paths: Set[str] = field(default_factory=set)
  head_read_stats: Dict[str, int] = field(default_factory=dict)


def maint_hints_path(archive_data_dir: str) -> str:
  return os.path.join(archive_data_dir, SYNC_ARCHIVE_MAINT_HINTS_BASENAME)


def _get_archive_discovery_worker_count(total_tasks: int) -> int:
  """Parallel head/sampled metadata reads; sole cap is ``get_sync_pool_process_cap()``."""
  if total_tasks <= 0:
    return 1
  configured = max(1, int(cfg.get_sync_pool_process_cap()))
  return max(1, min(total_tasks, configured))


def _maybe_log_parallel_task_progress(
    *,
    prefix: str,
    total: int,
    done: int,
    errors: int,
    started_mono: float,
    last_progress_mono: float,
    log_fn,
) -> float:
  if total < _ARCHIVE_METADATA_PROGRESS_MIN_PATHS or log_fn is None:
    return last_progress_mono
  if done >= total:
    return last_progress_mono
  now = time.monotonic()
  if done > 0 and (
      done % _ARCHIVE_METADATA_PROGRESS_EVERY_N == 0
      or (now - last_progress_mono) >= _ARCHIVE_METADATA_PROGRESS_INTERVAL_S
  ):
    log_fn(
        "%s: progress done=%d/%d errors=%d elapsed_s=%d"
        % (prefix, done, total, errors, int(now - started_mono)),
        flush=True,
    )
    return now
  return last_progress_mono


def _path_fingerprint(path: str) -> Optional[Tuple[int, int]]:
  """Return ``(mtime, size)`` hint key; prefers last find ``-printf`` cache."""
  from hpcperfstats.dbload.lib.sync_timedb_stats_find import lookup_path_fingerprint

  cached = lookup_path_fingerprint(path)
  if cached is not None:
    return cached
  try:
    st = os.stat(path)
    return int(st.st_mtime), int(st.st_size)
  except OSError:
    return None


def _host_dir_fingerprint(host_dir: str) -> Optional[Tuple[int, int]]:
  """Return ``(mtime, file_count)``; prefers last find ``-printf`` cache."""
  from hpcperfstats.dbload.lib.sync_timedb_stats_find import (
      lookup_host_dir_fingerprint,
  )

  cached = lookup_host_dir_fingerprint(host_dir)
  if cached is not None:
    return cached
  try:
    count = 0
    for entry in os.scandir(host_dir):
      if entry.is_file() and not entry.name.startswith("."):
        count += 1
    st = os.stat(host_dir)
    return int(st.st_mtime), count
  except OSError:
    return None


def load_archive_maint_hints(archive_data_dir: str) -> Optional[Dict[str, Any]]:
  if not cfg.get_sync_archive_maint_hints():
    return None
  from hpcperfstats.dbload.lib.sync_timedb_persistence import load_persistence_document

  path = maint_hints_path(archive_data_dir)
  data = load_persistence_document(path, "archive_maint_hints", default=None)
  if not isinstance(data, dict):
    return None
  version = data.get("version")
  if version not in (_MAINT_HINTS_VERSION, 1):
    return None
  return data


def _daily_tar_hint_identity(tar_path: str):
  if not tar_path or not os.path.isfile(tar_path):
    return None
  try:
    st = os.stat(tar_path)
    return int(st.st_mtime_ns), int(st.st_size)
  except OSError:
    return None


def prune_validated_days_hints(validated_days: Dict[str, Any]) -> Dict[str, Any]:
  """Drop validated-day entries when sealed or sibling tar identity changed."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      _archive_file_identity,
      daily_tar_path_from_compressed,
  )

  pruned: Dict[str, Any] = {}
  for gz_path, entry in (validated_days or {}).items():
    if not isinstance(entry, dict):
      continue
    gz_identity = _archive_file_identity(gz_path)
    if gz_identity is None:
      continue
    if (
        int(entry.get("mtime_ns", -1)) != gz_identity[0]
        or int(entry.get("size", -1)) != gz_identity[1]
    ):
      continue
    tar_path = daily_tar_path_from_compressed(gz_path)
    tar_identity = _daily_tar_hint_identity(tar_path)
    if tar_identity is not None and entry.get("tar_mtime_ns") is not None:
      if (
          int(entry.get("tar_mtime_ns", -1)) != tar_identity[0]
          or int(entry.get("tar_size", -1)) != tar_identity[1]
      ):
        continue
    pruned[gz_path] = entry
  return pruned


def prune_day_phases_hints(day_phases: Dict[str, Any]) -> Dict[str, Any]:
  """Drop day-phase entries when the daily ``.tar`` fingerprint changed.

  Sealed-only ``tar_dropped`` (or ``sealed``) phases with no sibling ``.tar``
  are retained so persistence resets / prune do not rediscover finished days.
  """
  pruned: Dict[str, Any] = {}
  for tar_path, value in (day_phases or {}).items():
    if isinstance(value, dict):
      phase = value.get("phase", "")
      stored_mtime = value.get("tar_mtime_ns")
      stored_size = value.get("tar_size")
    else:
      phase = value
      stored_mtime = None
      stored_size = None
    if not phase:
      continue
    tar_identity = _daily_tar_hint_identity(tar_path)
    if tar_identity is None:
      phase_text = str(phase)
      if phase_text in ("tar_dropped", "sealed"):
        zst_path, gz_path = compressed_sibling_paths(str(tar_path))
        if os.path.isfile(zst_path) or os.path.isfile(gz_path):
          pruned[tar_path] = value
      continue
    if stored_mtime is not None:
      if (
          int(stored_mtime) != tar_identity[0]
          or int(stored_size or -1) != tar_identity[1]
      ):
        continue
    pruned[tar_path] = value
  return pruned


def day_phase_hint_entry(tar_path: str, phase: str):
  """Build a hint entry with sibling ``.tar`` fingerprint for invalidation."""
  tar_identity = _daily_tar_hint_identity(tar_path)
  if tar_identity is None:
    return phase
  return {
      "phase": phase,
      "tar_mtime_ns": tar_identity[0],
      "tar_size": tar_identity[1],
  }


def _prune_hints_paths(hints_paths: Dict[str, Any]) -> Dict[str, Any]:
  pruned = {}
  for path, entry in (hints_paths or {}).items():
    if not isinstance(entry, dict):
      continue
    if not os.path.isfile(path):
      continue
    fp = _path_fingerprint(path)
    if fp is None:
      continue
    mtime, size = fp
    if int(entry.get("mtime", -1)) != mtime or int(entry.get("size", -1)) != size:
      continue
    pruned[path] = entry
  return pruned


def save_archive_maint_hints(
    archive_data_dir: str,
    *,
    host_dirs: Dict[str, Dict[str, int]],
    paths: Dict[str, Dict[str, Any]],
    validated_days: Dict[str, Dict[str, Any]],
    day_phases: Optional[Dict[str, str]] = None,
    debt_queue: Optional[list] = None,
) -> None:
  if not cfg.get_sync_archive_maint_hints():
    return
  path = maint_hints_path(archive_data_dir)
  from hpcperfstats.dbload.lib.sync_timedb_persistence import save_persistence_document

  payload = {
      "version": _MAINT_HINTS_VERSION,
      "host_dirs": host_dirs,
      "paths": _prune_hints_paths(paths),
      "validated_days": validated_days or {},
      "day_phases": day_phases or {},
      "debt_queue": list(debt_queue or []),
  }
  save_persistence_document(path, "archive_maint_hints", payload)


def _split_paths_by_hints(
    closed_paths: list,
    hints_data: Optional[Dict[str, Any]],
) -> Tuple[
    Dict[str, str],
    Dict[str, Tuple[str, int]],
    list,
    Dict[str, Dict[str, int]],
]:
  """Return (first_ts, head_identity, needs_read, host_dirs_fingerprints).

  Reuses stored path hints only when host-dir and per-path ``(mtime, size)``
  fingerprints still match (content-only edits with stable metadata are not
  invalidated here).
  """
  first_ts: Dict[str, str] = {}
  head_identity: Dict[str, Tuple[str, int]] = {}
  needs_read: list = []
  host_dirs: Dict[str, Dict[str, int]] = {}
  hinted_paths = (hints_data or {}).get("paths") or {}
  stored_host_dirs = (hints_data or {}).get("host_dirs") or {}

  paths_by_host: Dict[str, list] = {}
  for path in closed_paths:
    host_dir = os.path.dirname(path)
    paths_by_host.setdefault(host_dir, []).append(path)

  for host_dir, paths in paths_by_host.items():
    fp = _host_dir_fingerprint(host_dir)
    if fp is not None:
      mtime, count = fp
      host_dirs[host_dir] = {"mtime": mtime, "file_count": count}
    stored = stored_host_dirs.get(host_dir)
    host_unchanged = (
        stored is not None
        and fp is not None
        and int(stored.get("mtime", -2)) == fp[0]
        and int(stored.get("file_count", -2)) == fp[1]
    )
    for path in paths:
      entry = hinted_paths.get(path) if host_unchanged else None
      if isinstance(entry, dict) and entry.get("first_ts") is not None:
        fp_path = _path_fingerprint(path)
        if (
            fp_path is not None
            and int(entry.get("mtime", -1)) == fp_path[0]
            and int(entry.get("size", -1)) == fp_path[1]
        ):
          first_ts[path] = str(entry["first_ts"])
          if entry.get("host") is not None and entry.get("unix_second") is not None:
            head_identity[path] = (str(entry["host"]), int(entry["unix_second"]))
          else:
            needs_read.append(path)
          continue
      needs_read.append(path)
  return first_ts, head_identity, needs_read, host_dirs


def _read_head_metadata_one(path: str) -> Tuple[str, Optional[str], Optional[str], Optional[int]]:
  from hpcperfstats.dbload.lib.process_title import set_daemon_thread_title

  set_daemon_thread_title("", script_name="sync_timedb.py", role="archive-discovery")
  try:
    host, timestamp_utc = read_stats_file_head_identity(path)
  except Exception:
    return path, None, None, None
  if host is None or timestamp_utc is None:
    return path, None, None, None
  first_ts = str(int(timestamp_utc.timestamp()))
  unix_second = int(timestamp_utc.timestamp())
  return path, first_ts, str(host).strip(), unix_second


def _read_tail_metadata_one(path: str) -> Tuple[str, Optional[str], Optional[int]]:
  from hpcperfstats.dbload.lib.process_title import set_daemon_thread_title

  set_daemon_thread_title("", script_name="sync_timedb.py", role="archive-discovery")
  try:
    host, timestamp_utc = read_stats_file_tail_identity(path)
  except Exception:
    return path, None, None
  if host is None or timestamp_utc is None:
    return path, None, None
  return path, str(host).strip(), int(timestamp_utc.timestamp())


def collect_gate_identities_for_paths(
    paths,
    head_identity_by_path: Dict[str, Tuple[str, int]],
    *,
    log_fn=log_print,
) -> Tuple[Dict[str, Dict[str, Set[int]]], Dict[str, int]]:
  """Build per-path head+tail host→seconds maps; parallel EOF-backward tail reads."""
  from hpcperfstats.dbload.lib.sync_timedb_ingest_readiness import (
      head_tail_identity_as_gate_identities,
  )

  path_list = [
      path for path in (paths or [])
      if path in (head_identity_by_path or {})
  ]
  tail_identity_by_path: Dict[str, Tuple[str, int]] = {}
  errors = 0
  started = time.time()
  started_mono = time.monotonic()
  workers = _get_archive_discovery_worker_count(len(path_list)) if path_list else 0
  total_tasks = len(path_list)
  if log_fn and total_tasks >= _ARCHIVE_METADATA_PROGRESS_MIN_PATHS:
    log_fn(
        "Gate tail metadata: begin paths=%d workers=%d"
        % (total_tasks, workers),
        flush=True,
    )
  last_progress_mono = started_mono
  done = 0
  if path_list:
    for _path, packed, err in iter_bounded_thread_pool(
        path_list,
        _read_tail_metadata_one,
        max_workers=workers,
    ):
      done += 1
      if err is not None:
        errors += 1
        last_progress_mono = _maybe_log_parallel_task_progress(
            prefix="Gate tail metadata",
            total=total_tasks,
            done=done,
            errors=errors,
            started_mono=started_mono,
            last_progress_mono=last_progress_mono,
            log_fn=log_fn,
        )
        continue
      path, host, unix_second = packed
      if host is None or unix_second is None:
        errors += 1
        last_progress_mono = _maybe_log_parallel_task_progress(
            prefix="Gate tail metadata",
            total=total_tasks,
            done=done,
            errors=errors,
            started_mono=started_mono,
            last_progress_mono=last_progress_mono,
            log_fn=log_fn,
        )
        continue
      tail_identity_by_path[path] = (host, unix_second)
      last_progress_mono = _maybe_log_parallel_task_progress(
          prefix="Gate tail metadata",
          total=total_tasks,
          done=done,
          errors=errors,
          started_mono=started_mono,
          last_progress_mono=last_progress_mono,
          log_fn=log_fn,
      )
  gate_by_path = head_tail_identity_as_gate_identities(
      head_identity_by_path,
      tail_identity_by_path,
  )
  stats = {
      "paths": total_tasks,
      "read": total_tasks,
      "workers": workers if path_list else 0,
      "errors": errors,
      "elapsed_s": int(time.time() - started),
  }
  if log_fn and path_list:
    log_fn(
        "Gate tail metadata: paths=%d read=%d workers=%d errors=%d elapsed_s=%d"
        % (
            stats["paths"],
            stats["read"],
            stats["workers"],
            stats["errors"],
            stats["elapsed_s"],
        ),
        flush=True,
    )
  return gate_by_path, stats


def collect_head_metadata_for_paths(
    paths,
    *,
    hints_data=None,
    log_fn=log_print,
) -> Tuple[Dict[str, str], Dict[str, Tuple[str, int]], Dict[str, int]]:
  """Build first_timestamp and head_identity maps; parallel read for ``needs_read``."""
  first_ts_hinted, head_hinted, needs_read, _host_dirs = _split_paths_by_hints(
      list(paths), hints_data)
  first_timestamp_by_path = dict(first_ts_hinted)
  head_identity_by_path: Dict[str, Tuple[str, int]] = dict(head_hinted)
  errors = 0
  started = time.time()
  started_mono = time.monotonic()

  hinted_count = len(first_ts_hinted)
  read_total = len(needs_read)
  if needs_read:
    workers = _get_archive_discovery_worker_count(read_total)
    if log_fn and read_total >= _ARCHIVE_METADATA_PROGRESS_MIN_PATHS:
      log_fn(
          "Head metadata: begin paths=%d read=%d workers=%d"
          % (len(paths), read_total, workers),
          flush=True,
      )
    last_progress_mono = started_mono
    done = 0
    for _path, packed, err in iter_bounded_thread_pool(
        needs_read,
        _read_head_metadata_one,
        max_workers=workers,
    ):
      done += 1
      if err is not None:
        errors += 1
        last_progress_mono = _maybe_log_parallel_task_progress(
            prefix="Head metadata",
            total=read_total,
            done=done,
            errors=errors,
            started_mono=started_mono,
            last_progress_mono=last_progress_mono,
            log_fn=log_fn,
        )
        continue
      path, first_ts, host, unix_second = packed
      if first_ts is None or host is None or unix_second is None:
        errors += 1
        last_progress_mono = _maybe_log_parallel_task_progress(
            prefix="Head metadata",
            total=read_total,
            done=done,
            errors=errors,
            started_mono=started_mono,
            last_progress_mono=last_progress_mono,
            log_fn=log_fn,
        )
        continue
      first_timestamp_by_path[path] = first_ts
      head_identity_by_path[path] = (host, unix_second)
      last_progress_mono = _maybe_log_parallel_task_progress(
          prefix="Head metadata",
          total=read_total,
          done=done,
          errors=errors,
          started_mono=started_mono,
          last_progress_mono=last_progress_mono,
          log_fn=log_fn,
      )
  else:
    workers = 0

  stats = {
      "paths": len(paths),
      "hinted": hinted_count,
      "read": len(needs_read),
      "workers": workers if needs_read else 0,
      "errors": errors,
      "elapsed_s": int(time.time() - started),
  }
  if log_fn:
    log_fn(
        "Head metadata: paths=%d hinted=%d read=%d workers=%d errors=%d elapsed_s=%d"
        % (
            stats["paths"],
            stats["hinted"],
            stats["read"],
            stats["workers"],
            stats["errors"],
            stats["elapsed_s"],
        ),
        flush=True,
    )
  return first_timestamp_by_path, head_identity_by_path, stats


def remaining_raw_by_gz_from_mapping(mapping: Dict[str, list]) -> Dict[str, list]:
  return {
      normalize_daily_compressed_path(archive_path): list(stats_paths)
      for archive_path, stats_paths in mapping.items()
      if detect_compressed_format(archive_path) is not None and stats_paths
  }


def build_archive_maintenance_snapshot(
    archive_data_dir: str,
    host_name_ext: str,
    tgz_archive_dir: str,
    *,
    build_ready_set=True,
    log_fn=log_print,
) -> ArchiveMaintenanceSnapshot:
  """One collect pass, head+tail gate metadata, mapping, optional ready set."""
  from hpcperfstats.dbload.lib.sync_timedb_ingest_readiness import (
      build_head_ingest_ready_set,
  )

  snap_t0 = time.time()
  hints_data = load_archive_maint_hints(archive_data_dir)
  closed_paths = collect_stats_files_in_range(
      archive_data_dir, "backlog", None, host_name_ext, log_fn=log_fn)
  first_timestamp_by_path, head_identity_by_path, head_read_stats = (
      collect_head_metadata_for_paths(
          closed_paths, hints_data=hints_data, log_fn=log_fn)
  )
  if log_fn:
    log_fn(
        "Archive maintenance snapshot: head metadata complete paths=%d "
        "elapsed_s=%.3f"
        % (len(closed_paths), time.time() - snap_t0),
        flush=True,
    )
  gate_identities_by_path: Dict[str, Dict[str, Set[int]]] = {}
  gate_read_stats: Dict[str, int] = {}
  if build_ready_set:
    gate_identities_by_path, gate_read_stats = collect_gate_identities_for_paths(
        closed_paths,
        head_identity_by_path,
        log_fn=log_fn,
    )
  elif log_fn:
    log_fn(
        "Archive maintenance snapshot: gate tail skipped (build_ready_set=False)",
        flush=True,
    )
  mapping = build_archive_mapping(
      closed_paths,
      tgz_archive_dir,
      first_timestamp_by_path=first_timestamp_by_path,
  )
  remaining_raw_by_gz = remaining_raw_by_gz_from_mapping(mapping)
  if log_fn:
    log_fn(
        "Archive maintenance snapshot: mapping built days=%d closed_paths=%d "
        "elapsed_s=%.3f"
        % (len(mapping), len(closed_paths), time.time() - snap_t0),
        flush=True,
    )
  ready_paths: Set[str] = set()
  if build_ready_set:
    if log_fn:
      log_fn(
          "Archive maintenance snapshot: building head-ingest ready set "
          "paths=%d" % len(closed_paths),
          flush=True,
      )
    ready_paths = build_head_ingest_ready_set(
        closed_paths,
        gate_identities_by_path,
        log_fn=log_fn,
    )
  elif log_fn:
    log_fn(
        "Archive maintenance snapshot: ready_set skipped (build_ready_set=False)",
        flush=True,
    )
  if log_fn:
    log_fn(
        "Archive maintenance snapshot: complete ready_paths=%d elapsed_s=%.3f"
        % (len(ready_paths), time.time() - snap_t0),
        flush=True,
    )
  return ArchiveMaintenanceSnapshot(
      closed_paths=closed_paths,
      first_timestamp_by_path=first_timestamp_by_path,
      head_identity_by_path=head_identity_by_path,
      gate_identities_by_path=gate_identities_by_path,
      mapping=mapping,
      remaining_raw_by_gz=remaining_raw_by_gz,
      ready_paths=ready_paths,
      head_read_stats={
          **head_read_stats,
          "gate_tail_read_errors": gate_read_stats.get("errors", 0),
      },
  )


def snapshot_host_dirs_from_paths(closed_paths: list) -> Dict[str, Dict[str, int]]:
  host_dirs: Dict[str, Dict[str, int]] = {}
  seen = set()
  for path in closed_paths:
    host_dir = os.path.dirname(path)
    if host_dir in seen:
      continue
    seen.add(host_dir)
    fp = _host_dir_fingerprint(host_dir)
    if fp is not None:
      host_dirs[host_dir] = {"mtime": fp[0], "file_count": fp[1]}
  return host_dirs


def snapshot_paths_hint_entries(
    closed_paths: list,
    first_timestamp_by_path: Dict[str, str],
    head_identity_by_path: Dict[str, Tuple[str, int]],
) -> Dict[str, Dict[str, Any]]:
  entries: Dict[str, Dict[str, Any]] = {}
  for path in closed_paths:
    first_ts = first_timestamp_by_path.get(path)
    if first_ts is None:
      continue
    fp = _path_fingerprint(path)
    if fp is None:
      continue
    entry = {
        "mtime": fp[0],
        "size": fp[1],
        "first_ts": first_ts,
    }
    ident = head_identity_by_path.get(path)
    if ident is not None:
      entry["host"] = ident[0]
      entry["unix_second"] = ident[1]
    entries[path] = entry
  return entries


def log_archive_maintenance_snapshot_summary(snapshot: ArchiveMaintenanceSnapshot, *, log_fn=log_print):
  if not log_fn:
    return
  days = len(snapshot.mapping)
  seal_candidates = sum(
      1 for p in snapshot.mapping if detect_compressed_format(p) is not None
  )
  hrs = snapshot.head_read_stats or {}
  log_fn(
      "Archive maintenance snapshot: closed_paths=%d days=%d seal_candidates=%d "
      "head_read_tasks=%d head_read_workers=%d hinted=%d ready_paths=%d"
      % (
          len(snapshot.closed_paths),
          days,
          seal_candidates,
          hrs.get("read", 0),
          hrs.get("workers", 0),
          hrs.get("hinted", 0),
          len(snapshot.ready_paths),
      ),
      flush=True,
  )
