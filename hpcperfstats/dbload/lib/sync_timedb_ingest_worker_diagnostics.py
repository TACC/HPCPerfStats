"""
Cross-process ingest worker stage registry for pool stall diagnostics.

Attributes:
  _registry: Attribute.
  _worker_pool_kind: Attribute.
"""
from __future__ import annotations

from typing import Any, Iterator

import contextvars
import os
import time

_registry = None

_worker_pool_kind = contextvars.ContextVar("sync_timedb_worker_pool_kind", default=None)


def set_worker_diagnostics_registry(registry: Any) -> None:
  """
  Set the worker diagnostics registry.
  
  Args:
    registry (Any): Registry passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> set_worker_diagnostics_registry(None)  # doctest: +SKIP
  """
  global _registry
  _registry = registry


def get_worker_diagnostics_registry() -> Any:
  """
  Return the worker diagnostics registry.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_worker_diagnostics_registry()  # doctest: +SKIP
  """
  return _resolve_registry()


def _resolve_registry() -> Any:
  """
  Internal helper to resolve the registry.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _resolve_registry()  # doctest: +SKIP
  """
  registry = _registry
  if registry is not None:
    return registry
  try:
    from multiprocessing import current_process

    return getattr(current_process(), "_hpc_worker_diagnostics_registry", None)
  except Exception:
    return None


def get_worker_pool_kind() -> Any:
  """
  Return the worker pool kind.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_worker_pool_kind()  # doctest: +SKIP
  """
  return _worker_pool_kind.get()


def set_worker_pool_kind(pool_kind: Any) -> Any:
  """
  Set the worker pool kind.
  
  Args:
    pool_kind (Any): Pool kind passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> set_worker_pool_kind(None)  # doctest: +SKIP
  """
  return _worker_pool_kind.set(pool_kind)


def reset_worker_pool_kind(token: Any) -> None:
  """
  Reset worker pool kind.
  
  Args:
    token (Any): Token passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> reset_worker_pool_kind(None)  # doctest: +SKIP
  """
  _worker_pool_kind.reset(token)


def may_run_archive_members_populate_scan() -> Any:
  """
  True only on populate-pool workers.
  
  Returns:
    Any: Open return polymorphism from
    ``may_run_archive_members_populate_scan``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> may_run_archive_members_populate_scan()  # doctest: +SKIP
  """
  return get_worker_pool_kind() == "populate-pool"


def apply_ingest_pool_worker_init(
  script_name: Any,
  pool_kind: Any,
  registry: Any,
) -> None:
  """
  Apply the ingest pool worker init.
  
  Args:
    script_name (Any): Script name passed to this helper.
    pool_kind (Any): Pool kind passed to this helper.
    registry (Any): Registry passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> apply_ingest_pool_worker_init(None, None, None)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.process_title import apply_pool_worker_process_title

  apply_pool_worker_process_title(script_name, pool_kind)
  set_worker_pool_kind(pool_kind)
  set_worker_diagnostics_registry(registry)
  try:
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        drop_archive_members_redis_client,
    )

    # F14: drop any inherited process-global Redis client after spawn.
    drop_archive_members_redis_client()
  except Exception:
    pass
  try:
    from multiprocessing import current_process

    current_process()._hpc_worker_diagnostics_registry = registry
  except Exception:
    pass


def record_worker_stage(
  path: str,
  stage: Any,
  *,
  substage: Any | None = None,
  lookup_mode: Any | None = None,
  timeout_s: Any | None = None,
) -> None:
  """
  Record worker stage.
  
  Args:
    path (str): String for path.
    stage (Any): Mode or kind token selecting a code path.
    substage (Any | None): One of ``Any``, ``None``.
    lookup_mode (Any | None): One of ``Any``, ``None``.
    timeout_s (Any | None): One of ``Any``, ``None``.
  
  Returns:
    None
  
  Examples:
    >>> record_worker_stage("x", None, None, None, None)  # doctest: +SKIP
  """
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
  if timeout_s is not None:
    try:
      payload["timeout_s"] = "%.1f" % float(timeout_s)
    except (TypeError, ValueError):
      payload["timeout_s"] = str(timeout_s)
  pid = str(os.getpid())
  try:
    registry[pid] = payload
  except Exception:
    try:
      registry.update({pid: payload})
    except Exception:
      pass


def clear_worker_stage() -> None:
  """
  Clear worker stage.
  
  Returns:
    None
  
  Examples:
    >>> clear_worker_stage()  # doctest: +SKIP
  """
  registry = _resolve_registry()
  if registry is None:
    return
  pid = str(os.getpid())
  try:
    registry.pop(pid, None)
  except Exception:
    pass


def seed_dispatch_worker_stages(registry: Any, paths: Any) -> None:
  """
  Supervisor-side placeholders until pool workers record real stages.
  
  Args:
    registry (Any): Registry passed to this helper.
    paths (Any): Iterable of filesystem paths as strings.
  
  Returns:
    None
  
  Examples:
    >>> seed_dispatch_worker_stages(None, None)  # doctest: +SKIP
  """
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


def clear_dispatch_worker_stages(registry: Any, paths: Any) -> None:
  """
  Remove supervisor ``dispatch:`` placeholders when imap returns a path.
  
  Args:
    registry (Any): Registry passed to this helper.
    paths (Any): Iterable of filesystem paths as strings.
  
  Returns:
    None
  
  Examples:
    >>> clear_dispatch_worker_stages(None, None)  # doctest: +SKIP
  """
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


def update_worker_substage(substage: Any, **extra: Any) -> None:
  """
  Update the worker substage.
  
  Args:
    substage (Any): Substage passed to this helper.
    **extra (Any): Extra keyword arguments (``extra``); keys are ``str`` and
    value types match the wrapped protocol for this helper.
  
  Returns:
    None
  
  Examples:
    >>> update_worker_substage(None)  # doctest: +SKIP
  """
  registry = _resolve_registry()
  if registry is None:
    return
  pid = str(os.getpid())
  try:
    entry = dict(registry.get(pid) or {})
    if not entry:
      return
    entry["substage"] = str(substage)
    entry["t0"] = time.monotonic()
    for key, value in extra.items():
      if value is not None:
        entry[key] = str(value)
    registry[pid] = entry
  except Exception:
    pass


def count_worker_registry_entries(registry: Any) -> Any:
  """
  Count the worker registry entries.
  
  Args:
    registry (Any): Registry passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> count_worker_registry_entries(None)  # doctest: +SKIP
  """
  if registry is None:
    return 0
  try:
    return len(registry)
  except Exception:
    return 0


def format_worker_stages_snapshot(
  registry: Any,
  *,
  max_entries: int = 16,
  prefer_paths: Any | None = None,
) -> Any:
  """
  Format the worker stages snapshot.
  
  Args:
    registry (Any): Registry passed to this helper.
    max_entries (int): Integer value for max entries.
    prefer_paths (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> format_worker_stages_snapshot(None, 0, None)  # doctest: +SKIP
  """
  if registry is None:
    return "-"
  now = time.monotonic()
  prefer_norm = {
      os.path.normpath(str(path))
      for path in (prefer_paths or ())
      if path
  }
  ranked = []
  try:
    items = list(registry.items())
  except Exception:
    return "-"
  for pid, raw in items:
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
    line = "%s:%s:%s:%.0f" % (pid, stage, basename, age_s)
    stage_l = str(stage).lower()
    path_norm = os.path.normpath(path) if path else ""
    if path_norm and path_norm in prefer_norm:
      prefer = 0
    elif stage_l.startswith((
        "ingest", "parse", "db_", "archive_member", "dispatch",
    )):
      prefer = 1
    elif "populate" in stage_l:
      prefer = 3
    else:
      prefer = 2
    ranked.append((prefer, age_s, line))
  ranked.sort(key=lambda item: (item[0], item[1]))
  parts = [item[2] for item in ranked[: max(0, int(max_entries))]]
  return ",".join(parts) if parts else "-"


def iter_alive_pool_worker_pids(pool: Any) -> Iterator[Any]:
  """
  Yield string PIDs for alive workers in ``pool``.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> iter_alive_pool_worker_pids(None)  # doctest: +SKIP
  """
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


def worker_registry_shows_member_match_wait(
  registry: Any,
  *,
  pool: Any | None = None,
  alive_pids: Any | None = None,
  progress_grace_s: Any | None = None,
) -> Any:
  """
  True when an alive worker is in archive_member_lookup redis_wait.
  
  Args:
    registry (Any): Registry passed to this helper.
    pool (Any | None): One of ``Any``, ``None``.
    alive_pids (Any | None): One of ``Any``, ``None``.
    progress_grace_s (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> worker_registry_shows_member_match_wait(None, None, None, None)
  """
  if registry is None:
    return False
  import hpcperfstats.dbload.lib.conf_parser as cfg

  if progress_grace_s is None:
    try:
      progress_grace_s = float(
          cfg.get_sync_archive_members_redis_populate_max_seconds(),
      )
    except Exception:
      progress_grace_s = 7200.0
    if progress_grace_s <= 0.0:
      floor_s = float(cfg.get_sync_ingest_per_file_timeout_s())
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
    lookup_mode = str(raw.get("lookup_mode") or "")
    substage = str(raw.get("substage") or "")
    if lookup_mode != "redis_wait" and substage != "archive_member_lookup":
      continue
    if lookup_mode and lookup_mode != "redis_wait":
      continue
    t0 = raw.get("t0")
    if t0 is None:
      continue
    age_s = max(0.0, now - float(t0))
    if age_s < progress_grace_s:
      return True
  return False


def idle_pool_recover_skip_reason_for_registry_wait(
  paths: Any,
  registry: Any,
) -> Any:
  """
  Non-empty reason when pending paths show live redis_wait in the registry.
  
  Ghost ``dispatch:`` placeholders are ignored — only real worker PID entries
  whose ``path`` matches a pending normpath count. Skips idle recover/redispatch
  even when ``ingest_tar_hot`` has already cleared.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    registry (Any): Registry passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> idle_pool_recover_skip_reason_for_registry_wait(None, None)
  """
  if registry is None or not paths:
    return ""
  pending_norms = set()
  for path in paths:
    if not path:
      continue
    pending_norms.add(os.path.normpath(str(path)))
  if not pending_norms:
    return ""
  try:
    items = list(registry.items())
  except Exception:
    return ""
  for pid, raw in items:
    pid_s = str(pid)
    if pid_s.startswith("dispatch:"):
      continue
    if not isinstance(raw, dict):
      continue
    path = str(raw.get("path") or "")
    if not path or os.path.normpath(path) not in pending_norms:
      continue
    lookup_mode = str(raw.get("lookup_mode") or "")
    substage = str(raw.get("substage") or "")
    if lookup_mode == "redis_wait" or substage == "archive_member_lookup":
      if lookup_mode and lookup_mode != "redis_wait":
        continue
      return "registry_redis_wait path=%s" % os.path.basename(path)
  return ""


def worker_registry_shows_recent_progress(
  registry: Any,
  *,
  pool: Any | None = None,
  alive_pids: Any | None = None,
  progress_grace_s: Any | None = None,
) -> Any:
  """
  True when any alive worker has a registry stage younger than its ingest.
  
    budget.
  
  Args:
    registry (Any): Registry passed to this helper.
    pool (Any | None): One of ``Any``, ``None``.
    alive_pids (Any | None): One of ``Any``, ``None``.
    progress_grace_s (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> worker_registry_shows_recent_progress(None, None, None, None)
  """
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


def prune_stale_worker_stages(
  registry: Any,
  *,
  alive_pids: Any | None = None,
  max_age_s: float = 3600.0,
) -> None:
  """
  Prune stale worker stages.
  
  Args:
    registry (Any): Registry passed to this helper.
    alive_pids (Any | None): One of ``Any``, ``None``.
    max_age_s (float): Floating-point value for max age s.
  
  Returns:
    None
  
  Examples:
    >>> prune_stale_worker_stages(None, None, 0)  # doctest: +SKIP
  """
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
