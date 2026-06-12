"""Cross-process ingest worker stage registry for pool stall diagnostics."""
from __future__ import annotations

import os
import time

_registry = None


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


def apply_ingest_pool_worker_init(script_name, pool_kind, registry):
  from hpcperfstats.process_title import apply_pool_worker_process_title

  apply_pool_worker_process_title(script_name, pool_kind)
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
