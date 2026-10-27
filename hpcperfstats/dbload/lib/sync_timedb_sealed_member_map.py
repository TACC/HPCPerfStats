"""
Persisted member map of each sealed daily archive (T0 "is it in the zst").

Destructive archive gates (raw delete, corrupt-tar restore, tar drop) must
prove that another copy of the bytes exists **now** without streaming a
multi-GB ``.tar.zst`` per decision. ``atomic_seal_tar_to_zst`` already knows
the member map of the tar it just compressed; after ``zstd -t`` passes it
persists that map here, keyed by the sealed file's identity
``(inode, size, mtime_ns)``. A lookup is one ``stat`` plus a cached dict:
when the live identity differs from the recorded one (or no map exists) the
map is invalid, the caller **defers**, and one background rebuild streams the
sealed bytes once (check-cost tier T2, never on a delete or claim path).

Maps live beside the daily archives in ``{daily_dir}/.sync_timedb_sealed_
members/<day>.json``. They are self-validating by identity, so a persistence
contract reset does not need to remove them.

Attributes:
  SEALED_MEMBER_MAP_DIRNAME: Directory (under the daily archive dir) that
    holds one JSON map per sealed day.
  SEALED_MEMBER_MAP_KIND: Persistence kind used for load/save envelopes.
  _CACHE: Process-local cache ``map_path -> (identity, members)``.
  _CACHE_LOCK: Guards ``_CACHE``.
  _CACHE_MAX: Maximum cached day maps (oldest evicted first).
  _REBUILD_EXECUTOR: Lazily started daemon worker thread for T2 rebuilds.
  _REBUILD_QUEUE: Pending sealed paths for the rebuild worker.
  _REBUILD_LOCK: Guards the executor and ``_REBUILD_PENDING``.
  _REBUILD_PENDING: Normalized sealed paths with a queued or running rebuild.
  SEALED_INTEGRITY_CENSUS_BASENAME: A18 census file in the daily directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
from collections import OrderedDict
from typing import Any

from hpcperfstats.dbload.lib.print_utils import log_print
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
  load_persistence_document,
  save_persistence_document,
)

SEALED_MEMBER_MAP_DIRNAME = ".sync_timedb_sealed_members"
SEALED_MEMBER_MAP_KIND = "sealed_member_map"

_CACHE: OrderedDict[str, tuple[dict[str, int], dict[str, int]]] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 8
_REBUILD_EXECUTOR: threading.Thread | None = None
_REBUILD_QUEUE: queue.Queue[str] = queue.Queue()
_REBUILD_LOCK = threading.Lock()
_REBUILD_PENDING: set[str] = set()


def archive_identity(path: str) -> dict[str, int] | None:
  """
  Return the on-disk identity ``{ino, size, mtime_ns}`` of an archive file.

  The identity changes on any rewrite (``os.replace`` gives a new inode) and on
  any in-place append (size and mtime), so equality proves the bytes a check
  saw are the bytes on disk now.

  Args:
    path (str): Archive path (open ``.tar`` or sealed ``.tar.zst``/``.gz``).

  Returns:
    dict[str, int] | None: Identity mapping, or None when the file is absent.

  Examples:
    >>> archive_identity("/nonexistent/2026-01-01.tar.zst") is None
    True
  """
  try:
    st = os.stat(path)
  except OSError:
    return None
  return {
    "ino": int(st.st_ino),
    "size": int(st.st_size),
    "mtime_ns": int(st.st_mtime_ns),
  }


def sealed_member_map_path(sealed_path: str) -> str:
  """
  Return the JSON map path for one sealed daily archive.

  Args:
    sealed_path (str): ``YYYY-MM-DD.tar.zst`` (or legacy ``.tar.gz``) path.

  Returns:
    str: ``{dirname}/.sync_timedb_sealed_members/YYYY-MM-DD.json``.

  Examples:
    >>> sealed_member_map_path("/a/2026-01-01.tar.zst")
    '/a/.sync_timedb_sealed_members/2026-01-01.json'
  """
  norm = os.path.normpath(str(sealed_path))
  day = os.path.basename(norm).split(".", 1)[0]
  return os.path.join(
    os.path.dirname(norm), SEALED_MEMBER_MAP_DIRNAME, f"{day}.json"
  )


def _members_sha256(members: dict[str, int]) -> str:
  """
  Return a stable digest of a member map (sorted ``name:size`` lines).

  Args:
    members (dict[str, int]): Member name to byte size.

  Returns:
    str: Hex SHA-256 digest.

  Examples:
    >>> len(_members_sha256({"a": 1}))
    64
  """
  digest = hashlib.sha256()
  for name in sorted(members):
    digest.update(f"{name}:{int(members[name])}\n".encode())
  return digest.hexdigest()


def write_sealed_member_map(
  sealed_path: str,
  members: dict[str, int],
  *,
  source: str,
  identity: dict[str, int] | None = None,
) -> bool:
  """
  Persist ``members`` as the authoritative map of ``sealed_path``.

  Call only with a map taken from the sealed bytes' own source: the tar that
  was just compressed and passed ``zstd -t`` (``source=seal``) or a complete
  stream of the sealed file (``source=rebuild``). ``identity`` should be the
  sealed file identity observed before that source was read; when it no longer
  matches the file on disk the write is skipped (the file changed underneath).

  Args:
    sealed_path (str): Sealed daily archive path.
    members (dict[str, int]): Member name to byte size.
    source (str): ``seal`` or ``rebuild`` (recorded for census).
    identity (dict[str, int] | None): Identity seen before reading
      ``members``; None reads it now (seal path, under the seal lock).

  Returns:
    bool: True when the map was written.

  Examples:
    >>> write_sealed_member_map("/nonexistent.tar.zst", {}, source="seal")
    False
  """
  live = archive_identity(sealed_path)
  if live is None or (identity is not None and identity != live):
    return False
  map_path = sealed_member_map_path(sealed_path)
  clean = {str(k): int(v) for k, v in (members or {}).items()}
  payload = {
    "sealed_path": os.path.normpath(str(sealed_path)),
    "zst_identity": live,
    "entries": clean,
    "members_sha256": _members_sha256(clean),
    "source": str(source),
    "written_at": time.time(),
  }
  try:
    os.makedirs(os.path.dirname(map_path), exist_ok=True)
    save_persistence_document(map_path, SEALED_MEMBER_MAP_KIND, payload)
  except OSError as exc:
    log_print(
      f"WARN: sealed_member_map write failed path={map_path} err={exc}",
      flush=True,
    )
    return False
  with _CACHE_LOCK:
    _CACHE[map_path] = (live, clean)
    _CACHE.move_to_end(map_path)
    while len(_CACHE) > _CACHE_MAX:
      _CACHE.popitem(last=False)
  return True


def sealed_members_lookup(
  sealed_path: str,
  *,
  request_rebuild: bool = True,
) -> dict[str, int] | None:
  """
  Return the member map of ``sealed_path`` when a valid map exists (T0).

  Cost: one ``stat`` plus a cached dict (one JSON read on a cache miss).
  Never decompresses. When the map is missing, unreadable, or recorded for a
  different identity the result is None and, with ``request_rebuild``, one
  background stream rebuild is queued for that day.

  Args:
    sealed_path (str): Sealed daily archive path.
    request_rebuild (bool): Queue a background rebuild when invalid.

  Returns:
    dict[str, int] | None: Member name to size, or None when unknown.

  Examples:
    >>> sealed_members_lookup("/nonexistent.tar.zst", request_rebuild=False)
  """
  live = archive_identity(sealed_path)
  if live is None:
    return None
  map_path = sealed_member_map_path(sealed_path)
  with _CACHE_LOCK:
    cached = _CACHE.get(map_path)
    if cached is not None and cached[0] == live:
      _CACHE.move_to_end(map_path)
      return cached[1]
  doc = load_persistence_document(map_path, SEALED_MEMBER_MAP_KIND)
  members = None
  if isinstance(doc, dict) and doc.get("zst_identity") == live:
    entries = doc.get("entries")
    if isinstance(entries, dict) and doc.get("members_sha256") == (
      _members_sha256(entries)
    ):
      members = {str(k): int(v) for k, v in entries.items()}
  if members is None:
    if request_rebuild:
      request_sealed_member_map_rebuild(sealed_path)
    return None
  with _CACHE_LOCK:
    _CACHE[map_path] = (live, members)
    _CACHE.move_to_end(map_path)
    while len(_CACHE) > _CACHE_MAX:
      _CACHE.popitem(last=False)
  return members


def raw_path_in_sealed_members(raw_path: str, members: dict[str, int]) -> bool:
  """
  Return True when ``raw_path`` is a sealed member with the same byte size.

  Args:
    raw_path (str): Raw stats file on disk.
    members (dict[str, int]): Sealed member map (from the lookup).

  Returns:
    bool: True when name and size match; False when absent, different size,
    or the raw file cannot be stat'ed.

  Examples:
    >>> raw_path_in_sealed_members("/nonexistent/1", {"nonexistent/1": 3})
    False
  """
  name = str(raw_path).lstrip("/")
  if name not in members:
    return False
  try:
    return os.path.getsize(raw_path) == int(members[name])
  except OSError:
    return False


def rebuild_sealed_member_map_sync(sealed_path: str) -> bool:
  """
  Stream ``sealed_path`` once and persist its member map (T2, background).

  The sealed identity is read before the stream and must still match after
  it, so a reseal racing the stream cannot be recorded under the new file.

  Args:
    sealed_path (str): Sealed daily archive path.

  Returns:
    bool: True when a complete readable stream produced a persisted map.

  Examples:
    >>> rebuild_sealed_member_map_sync("/nonexistent.tar.zst")
    False
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    _scan_compressed_archive_members_and_readable,
  )

  before = archive_identity(sealed_path)
  if before is None:
    return False
  started = time.monotonic()
  try:
    readable, members = _scan_compressed_archive_members_and_readable(
      sealed_path
    )
  except (OSError, RuntimeError, TimeoutError) as exc:
    log_print(
      f"WARN: sealed_member_map rebuild failed path={sealed_path} err={exc}",
      flush=True,
    )
    return False
  if not readable:
    log_print(
      f"WARN: sealed_member_map rebuild skipped unreadable path={sealed_path}",
      flush=True,
    )
    return False
  ok = write_sealed_member_map(
    sealed_path, dict(members), source="rebuild", identity=before
  )
  log_print(
    f"INFO: sealed_member_map rebuild path={sealed_path} ok={ok} "
    f"members={len(members)} gate=sealed_map_rebuild tier=T2 "
    f"elapsed_s={time.monotonic() - started:.1f}",
    flush=True,
  )
  return ok


def request_sealed_member_map_rebuild(sealed_path: str) -> bool:
  """
  Queue one background rebuild for ``sealed_path`` (deduplicated per path).

  Rebuilds run one at a time on a dedicated thread so the full-stream cost
  never lands on a delete, claim, or ingest path.

  Args:
    sealed_path (str): Sealed daily archive path.

  Returns:
    bool: True when a new rebuild was queued; False when one is pending.

  Examples:
    >>> request_sealed_member_map_rebuild(
    ...   "/nonexistent.tar.zst"
    ... )  # doctest: +SKIP
    True
  """
  global _REBUILD_EXECUTOR
  norm = os.path.normpath(str(sealed_path))
  with _REBUILD_LOCK:
    if norm in _REBUILD_PENDING:
      return False
    _REBUILD_PENDING.add(norm)
    _REBUILD_QUEUE.put(norm)
    if _REBUILD_EXECUTOR is None or not _REBUILD_EXECUTOR.is_alive():
      # Daemon thread: a T2 stream must never hold up process shutdown.
      _REBUILD_EXECUTOR = threading.Thread(
        target=_rebuild_worker, name="sealed-map-rebuild", daemon=True
      )
      _REBUILD_EXECUTOR.start()
  return True


def _rebuild_worker() -> None:
  """
  Drain queued sealed-map rebuilds one at a time (daemon thread body).

  Returns:
    None

  Examples:
    >>> _rebuild_worker  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.process_title import set_daemon_thread_title

  set_daemon_thread_title(
    "sync_timedb.py", script_name="sync_timedb.py", role="sealed-map-rebuild"
  )
  while True:
    norm = _REBUILD_QUEUE.get()
    try:
      rebuild_sealed_member_map_sync(norm)
    except Exception as exc:
      log_print(
        f"WARN: sealed_member_map rebuild crashed path={norm} err={exc}",
        flush=True,
      )
    finally:
      with _REBUILD_LOCK:
        _REBUILD_PENDING.discard(norm)


def sealed_member_map_census(daily_dir: str) -> dict[str, Any]:
  """
  Summarize map coverage for operator census (no decompression).

  Args:
    daily_dir (str): Daily archive directory.

  Returns:
    dict[str, Any]: ``maps`` count and ``valid`` count (identity matches).

  Examples:
    >>> sealed_member_map_census("/nonexistent")
    {'maps': 0, 'valid': 0}
  """
  root = os.path.join(str(daily_dir), SEALED_MEMBER_MAP_DIRNAME)
  maps = valid = 0
  try:
    names = sorted(os.listdir(root))
  except OSError:
    return {"maps": 0, "valid": 0}
  for name in names:
    if not name.endswith(".json"):
      continue
    maps += 1
    try:
      with open(os.path.join(root, name), encoding="utf-8") as fh:
        doc = json.load(fh)
    except OSError, ValueError:
      continue
    sealed = str(doc.get("sealed_path") or "")
    if sealed and archive_identity(sealed) == doc.get("zst_identity"):
      valid += 1
  return {"maps": maps, "valid": valid}


SEALED_INTEGRITY_CENSUS_BASENAME = ".sync_timedb_sealed_integrity.json"


def _load_integrity_census(path: str) -> dict[str, Any]:
  """
  Load the sealed-integrity census (empty when missing or unreadable).

  Args:
    path (str): Census JSON path.

  Returns:
    dict[str, Any]: ``day -> result`` mapping.

  Examples:
    >>> _load_integrity_census("/nonexistent.json")
    {}
  """
  try:
    with open(path, encoding="utf-8") as fh:
      doc = json.load(fh)
  except OSError, ValueError:
    return {}
  return doc if isinstance(doc, dict) else {}


def sealed_integrity_sweep_once(
  daily_dir: str,
  *,
  log_fn: Any = log_print,
) -> dict[str, Any] | None:
  """
  Check one sealed daily archive end to end (A18; the routine T2 consumer).

  Oldest unchecked (or changed) ``.tar.zst`` first, one per call, so the
  cost is bounded per idle reconstruct pass. ``zstd -t`` plus a full member
  stream; a pass refreshes the sealed member map. A failure **never** deletes
  or rewrites the sealed file: it is recorded in
  ``{daily_dir}/.sync_timedb_sealed_integrity.json`` with the readable member
  count, the expected count (from the map when present), whether an open tar
  exists (``action=reseal_from_tar``; day close then reseals, DE-9) or not
  (``action=operator_restore_needed``), and logged at ERROR.

  Args:
    daily_dir (str): Daily archive directory.
    log_fn (Any): Logger callable.

  Returns:
    dict[str, Any] | None: The recorded result, or None when nothing is due.

  Examples:
    >>> sealed_integrity_sweep_once("/nonexistent") is None
    True
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    _scan_compressed_archive_members_and_readable,
  )
  from hpcperfstats.dbload.lib.zstd_cli import zstd_test

  census_path = os.path.join(daily_dir, SEALED_INTEGRITY_CENSUS_BASENAME)
  census = _load_integrity_census(census_path)
  try:
    names = sorted(n for n in os.listdir(daily_dir) if n.endswith(".tar.zst"))
  except OSError:
    return None
  for name in names:
    day = name.split(".", 1)[0]
    sealed = os.path.join(daily_dir, name)
    live = archive_identity(sealed)
    if live is None or (census.get(day) or {}).get("zst_identity") == live:
      continue
    expected = sealed_members_lookup(sealed, request_rebuild=False)
    try:
      zstd_test(sealed, 1)
      frame_ok = True
    except Exception:
      frame_ok = False
    readable, members = _scan_compressed_archive_members_and_readable(sealed)
    ok = frame_ok and readable and (expected is None or members == expected)
    tar_path = sealed[: -len(".zst")]
    result = {
      "zst_identity": live,
      "checked_at": time.time(),
      "status": "ok" if ok else "failed",
      "readable_members": len(members or {}),
      "expected_members": None if expected is None else len(expected),
      "tar_present": os.path.isfile(tar_path),
    }
    if ok:
      write_sealed_member_map(
        sealed, dict(members), source="sweep", identity=live
      )
    else:
      result["action"] = (
        "reseal_from_tar"
        if result["tar_present"]
        else "operator_restore_needed"
      )
      log_print(
        f"ERROR: sealed integrity sweep failed path={sealed} "
        f"frame_ok={frame_ok} readable_members={result['readable_members']} "
        f"expected_members={result['expected_members']} "
        f"action={result['action']} (file left untouched)",
        flush=True,
      )
    census[day] = result
    tmp = census_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
      json.dump(census, fh, sort_keys=True)
    os.replace(tmp, census_path)
    return result
  return None
