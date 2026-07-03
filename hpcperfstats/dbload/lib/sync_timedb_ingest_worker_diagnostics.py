"""Cross-process ingest worker stage registry for pool stall diagnostics."""
from __future__ import annotations

import contextvars
import os
import time

_registry = None

_worker_pool_kind = contextvars.ContextVar("sync_timedb_worker_pool_kind", default=None)


def set_worker_diagnostics_registry(registry):
  global _registry
  _registry = registry


def get_worker_diagnostics_registry():
  return _resolve_registry()


def _resolve_registry():
  registry = _registry
  if registry is not None:
    return registry
  try:
    from multiprocessing import current_process

    return getattr(current_process(), "_hpc_worker_diagnostics_registry", None)
  except Exception:
    return None


def get_worker_pool_kind():
  return _worker_pool_kind.get()


def set_worker_pool_kind(pool_kind):
  return _worker_pool_kind.set(pool_kind)


def reset_worker_pool_kind(token):
  _worker_pool_kind.reset(token)


def may_run_archive_members_populate_scan():
  """True only on populate-pool workers."""
  return get_worker_pool_kind() == "populate-pool"


def apply_ingest_pool_worker_init(script_name, pool_kind, registry):
  from hpcperfstats.dbload.lib.process_title import apply_pool_worker_process_title

  apply_pool_worker_process_title(script_name, pool_kind)
  set_worker_pool_kind(pool_kind)
  set_worker_diagnostics_registry(registry)
  try:
    from multiprocessing import current_process

    current_process()._hpc_worker_diagnostics_registry = registry
  except Exception:
    pass


def record_worker_stage(path, stage, *, substage=None, lookup_mode=None):
  registry = _resolve_registry()
  if registry is None:
    return
  payload = {
      "path": str(path or ""),
      "stage": str(stage or ""),
      "t0": time.monotonic(),
  }
  if substage:
    payload["substage"] = str(substage)
  if lookup_mode:
    payload["lookup_mode"] = str(lookup_mode)
  pid = str(os.getpid())
  try:
    registry[pid] = payload
  except Exception:
    try:
      registry.update({pid: payload})
    except Exception:
      pass


def clear_worker_stage():
  registry = _resolve_registry()
  if registry is None:
    return
  pid = str(os.getpid())
  try:
    registry.pop(pid, None)
  except Exception:
    pass


def seed_dispatch_worker_stages(registry, paths):
  """Supervisor-side placeholders until pool workers record real stages."""
  if registry is None:
    return
  now = time.monotonic()
  for path in paths or ():
    if not path:
      continue
    key = "dispatch:%s" % os.path.normpath(path)
    try:
      registry[key] = {
          "path": str(path),
          "stage": "dispatched",
          "t0": now,
      }
    except Exception:
      try:
        registry.update({
            key: {
                "path": str(path),
                "stage": "dispatched",
                "t0": now,
            },
        })
      except Exception:
        pass


def clear_dispatch_worker_stages(registry, paths):
  """Remove supervisor ``dispatch:`` placeholders when imap returns a path."""
  if registry is None:
    return
  for path in paths or ():
    if not path:
      continue
    key = "dispatch:%s" % os.path.normpath(path)
    try:
      registry.pop(key, None)
    except Exception:
      pass


def update_worker_substage(substage, **extra):
  registry = _resolve_registry()
  if registry is None:
    return
  pid = str(os.getpid())
  try:
    entry = dict(registry.get(pid) or {})
    if not entry:
      return
    entry["substage"] = str(substage)
    for key, value in extra.items():
      if value is not None:
        entry[key] = str(value)
    registry[pid] = entry
  except Exception:
    pass


def count_worker_registry_entries(registry):
  if registry is None:
    return 0
  try:
    return len(registry)
  except Exception:
    return 0


def format_worker_stages_snapshot(registry, *, max_entries=16):
  if registry is None:
    return "-"
  now = time.monotonic()
  parts = []
  try:
    items = list(registry.items())
  except Exception:
    return "-"
  for pid, raw in items[: max(0, int(max_entries))]:
    if not isinstance(raw, dict):
      continue
    path = raw.get("path") or ""
    basename = os.path.basename(path) if path else "-"
    stage = raw.get("substage") or raw.get("stage") or "-"
    lookup_mode = raw.get("lookup_mode")
    if lookup_mode:
      stage = "%s:%s" % (stage, lookup_mode)
    t0 = raw.get("t0")
    age_s = max(0.0, now - float(t0)) if t0 is not None else 0.0
    parts.append("%s:%s:%s:%.0f" % (pid, stage, basename, age_s))
  return ",".join(parts) if parts else "-"


def iter_alive_pool_worker_pids(pool):
  """Yield string PIDs for alive workers in ``pool``."""
  if pool is None:
    return
  try:
    from hpcperfstats.dbload.lib.multiprocessing_pool_health import (
        iter_pool_worker_processes,
    )
  except Exception:
    return
  for proc in iter_pool_worker_processes(pool):
    is_alive_fn = getattr(proc, "is_alive", None)
    if callable(is_alive_fn) and is_alive_fn():
      pid = getattr(proc, "pid", None)
      if pid is not None:
        yield str(pid)


def worker_registry_shows_recent_progress(
    registry,
    *,
    pool=None,
    alive_pids=None,
    progress_grace_s=None,
):
  """True when any alive worker has a registry stage younger than its ingest budget."""
  if registry is None:
    return False
  import hpcperfstats.dbload.lib.conf_parser as cfg
  from hpcperfstats.dbload.lib.sync_timedb_ingest_timeout import (
      resolve_ingest_per_file_timeout_s,
  )

  floor_s = float(cfg.get_sync_ingest_per_file_timeout_s())
  if progress_grace_s is None:
    progress_grace_s = floor_s if floor_s > 0.0 else 900.0
  else:
    progress_grace_s = float(progress_grace_s)
  if alive_pids is None and pool is not None:
    alive_pids = set(iter_alive_pool_worker_pids(pool))
  elif alive_pids is not None:
    alive_pids = {str(pid) for pid in alive_pids}
  else:
    alive_pids = set()
  now = time.monotonic()
  try:
    items = list(registry.items())
  except Exception:
    return False
  for pid, raw in items:
    pid_s = str(pid)
    if pid_s.startswith("dispatch:"):
      continue
    if alive_pids and pid_s not in alive_pids:
      continue
    if not isinstance(raw, dict):
      continue
    t0 = raw.get("t0")
    if t0 is None:
      continue
    age_s = max(0.0, now - float(t0))
    path = raw.get("path") or ""
    budget_s = resolve_ingest_per_file_timeout_s(str(path)) if path else progress_grace_s
    if budget_s <= 0.0:
      budget_s = progress_grace_s
    if age_s < budget_s:
      return True
  return False


def prune_stale_worker_stages(registry, *, alive_pids=None, max_age_s=3600.0):
  if registry is None:
    return
  now = time.monotonic()
  alive = {str(pid) for pid in (alive_pids or ())}
  try:
    for pid, raw in list(registry.items()):
      if pid in alive:
        continue
      if not isinstance(raw, dict):
        registry.pop(pid, None)
        continue
      t0 = raw.get("t0")
      if t0 is None or (now - float(t0)) >= float(max_age_s):
        registry.pop(pid, None)
  except Exception:
    pass
