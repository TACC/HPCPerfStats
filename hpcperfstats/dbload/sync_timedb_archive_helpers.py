"""Pure helpers for sync_timedb archiving, tar utilities, and file discovery (no Django). Used by sync_timedb and by unit tests."""
import contextlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import re
import shutil
import subprocess
import tarfile
import time
from collections import defaultdict
from datetime import datetime, timedelta

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload.archive_compress import (
    DAILY_ARCHIVE_GZ_SUFFIX,
    DAILY_ARCHIVE_ZST_SUFFIX,
    archive_member_maps_equivalent,
    compressed_sibling_paths,
    daily_compressed_path_for_date,
    daily_tar_path_from_compressed,
    detect_compressed_format,
    normalize_daily_compressed_path,
    sum_member_bytes,
    zstd_long_flags,
)
from hpcperfstats.dbload.zstd_cli import (
    decompress_compressed_to_tar,
    zstd_decompress_stdout,
    zstd_executable,
    zstd_gzip_decompress_stdout,
    zstd_gzip_supported,
    zstd_compress_tar_to_file,
    zstd_test,
)
from hpcperfstats.dbload.sync_timedb_parsing import parse_first_timestamp_line
from hpcperfstats.file_locking import (
    LOCK_EXPIRY_SECONDS,
    LOCK_SUFFIX,
    file_read_lock_wait,
    file_write_lock,
)
from hpcperfstats.print_utils import log_print

archive_zstd_thread_count = cfg.get_archive_zstd_threads()

_DAILY_TAR_BASENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.tar$")


def _get_archive_validation_worker_count(total_items):
  """Bounded worker count for archive read/validation fanout."""
  if total_items <= 0:
    return 1
  env = os.environ.get("SYNC_ARCHIVE_VALIDATION_WORKERS", "").strip()
  if env:
    try:
      configured = max(1, int(env))
    except ValueError:
      configured = max(1, int(cfg.get_sync_archive_pool_processes()))
  else:
    configured = max(1, int(cfg.get_sync_archive_pool_processes()))
  return max(1, min(total_items, configured))


def _iter_archive_validation_results_stream(
    gz_paths,
    *,
    log_fn=log_print,
    validation_cache=None,
):
  """Yield ``(gz_path, ok, members)`` as validations complete.

  - Keeps apply stage serial by yielding one completed result at a time.
  - Uses bounded thread fanout for read/validation only.
  - Uses shared validation_cache only on serial path (thread-safe by design).
  """
  gz_paths = list(gz_paths)
  if not gz_paths:
    return
  workers = _get_archive_validation_worker_count(len(gz_paths))
  if workers <= 1:
    for gz_path in gz_paths:
      ok, members = validate_sealed_daily_archive_for_raw_removal(
          gz_path,
          log_fn=log_fn,
          validation_cache=validation_cache,
      )
      yield gz_path, ok, members
    return

  def _validate_one(gz_path):
    # Avoid shared mutable cache updates across threads.
    return validate_sealed_daily_archive_for_raw_removal(
        gz_path,
        log_fn=log_fn,
        validation_cache=None,
    )

  with ThreadPoolExecutor(max_workers=workers) as executor:
    future_to_gz = {executor.submit(_validate_one, gz_path): gz_path for gz_path in gz_paths}
    for future in as_completed(future_to_gz):
      gz_path = future_to_gz[future]
      try:
        ok, members = future.result()
      except Exception as exc:
        if log_fn:
          log_fn(
              "Skipping removal: validation worker error for %s (%s)" % (gz_path, exc),
              flush=True,
          )
        ok, members = False, None
      yield gz_path, ok, members


def _log_archive_validation_summary(
    *,
    log_fn,
    validation_targets_count,
    workers,
    success_count,
    failed_count,
    validation_started,
    validation_cache,
):
  if not log_fn:
    return
  log_fn(
      "Archive validation parallel summary archives=%d workers=%d success=%d failed=%d elapsed_s=%.3f"
      % (
          validation_targets_count,
          workers,
          success_count,
          failed_count,
          max(0.0, time.time() - validation_started),
      ),
      flush=True,
  )
  log_fn(
      "Archive validation cache summary hits=%d misses=%d"
      % (int(validation_cache["hits"]), int(validation_cache["misses"])),
      flush=True,
  )


def _is_lock_file_name(name):
  """Return True for sidecar/advisory lock files (e.g. *.fnctl.lock)."""
  # We intentionally skip generic *.lock too, because different lock
  # implementations may exist on the filesystem (and we don't want them
  # mistaken for stats data files during archive discovery).
  return name.endswith(LOCK_SUFFIX) or name.endswith(".lock")


def _remove_read_lock_sidecar(target_path):
  """Best-effort cleanup for helper read-lock sidecars."""
  try:
    os.remove("%s%s" % (target_path, LOCK_SUFFIX))
  except OSError:
    pass


def read_stats_file_head_identity(stats_fname, parse_first_ts_fn=None):
  """Return ``(host, timestamp_utc)`` from the first stats timestamp line in the file.

  ``host`` is the monitor hostname token from file content (same as ``host_data.host``
  after ingest), not the archive subdirectory name in ``stats_fname``.
  """
  if parse_first_ts_fn is None:
    parse_first_ts_fn = parse_first_timestamp_line
  try:
    with file_read_lock_wait(stats_fname):
      with open(stats_fname, "r") as f:
        head = []
        for line in f:
          head.append(line)
          stripped = line.lstrip()
          if stripped and stripped[0].isdigit():
            break
  except OSError:
    return None, None
  finally:
    _remove_read_lock_sidecar(stats_fname)
  t, _jid, host = parse_first_ts_fn(head)
  if t is None or not host:
    return None, None
  from datetime import datetime, timezone

  # Gate and duplicate detection bucket by Unix second; ingest may store subseconds.
  timestamp_utc = datetime.fromtimestamp(int(float(t)), tz=timezone.utc)
  return str(host).strip(), timestamp_utc


def _read_first_timestamp_from_stats_file(stats_fname, parse_first_ts_fn):
  """Read minimal file head and parse first stats timestamp string."""
  _host, timestamp_utc = read_stats_file_head_identity(stats_fname, parse_first_ts_fn)
  if timestamp_utc is None:
    return None
  return str(int(timestamp_utc.timestamp()))


def collect_lock_sidecar_stats(directory, stale_after_seconds=LOCK_EXPIRY_SECONDS):
  """Return lock sidecar diagnostics for a directory tree."""
  if not directory or not os.path.isdir(directory):
    return {
        "lock_files": 0,
        "stale_lock_files": 0,
        "oldest_lock_age_seconds": 0.0,
    }
  now = time.time()
  lock_count = 0
  stale_count = 0
  oldest_age = 0.0
  for root, _dirs, files in os.walk(directory):
    for name in files:
      if not _is_lock_file_name(name):
        continue
      lock_count += 1
      path = os.path.join(root, name)
      try:
        age = max(0.0, now - os.path.getmtime(path))
      except OSError:
        continue
      oldest_age = max(oldest_age, age)
      if age > stale_after_seconds:
        stale_count += 1
  return {
      "lock_files": lock_count,
      "stale_lock_files": stale_count,
      "oldest_lock_age_seconds": oldest_age,
  }


def get_tar_member_name(file_path):
  """Return the name used for a file inside a tar (path without leading slash)."""
  return file_path.lstrip("/")


def _tar_list_executable():
  """GNU/BSD ``tar`` for ``tar tf`` integrity checks (same family as append)."""
  return shutil.which("tar") or "/bin/tar"


def _tar_readable_via_decompress_tar_pipe(decompress_cmd, tar_bin):
  """Full list scan: ``decompress -c | tar tf -`` (both must exit 0)."""
  p_decomp = subprocess.Popen(
      decompress_cmd,
      stdout=subprocess.PIPE,
      stderr=subprocess.DEVNULL,
  )
  try:
    p_tar = subprocess.Popen(
        [tar_bin, "tf", "-"],
        stdin=p_decomp.stdout,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
  except Exception:
    p_decomp.kill()
    try:
      p_decomp.wait(timeout=30)
    except (OSError, subprocess.SubprocessError):
      pass
    raise
  if p_decomp.stdout is not None:
    p_decomp.stdout.close()
  p_tar.communicate()
  decomp_rc = p_decomp.wait()
  return p_tar.returncode == 0 and decomp_rc == 0


def _tar_gz_readable_via_zstd_gzip_tar_pipe(gz_path, tar_bin, num_threads):
  if not zstd_gzip_supported():
    return False
  cmd = [
      zstd_executable(),
      "-d",
      "--format=gzip",
      "-c",
      "-T%d" % max(1, int(num_threads)),
      "-q",
      gz_path,
  ]
  return _tar_readable_via_decompress_tar_pipe(cmd, tar_bin)


def _tar_zst_readable_via_zstd_tar_pipe(zst_path, tar_bin, num_threads):
  if not shutil.which("zstd"):
    return False
  cmd = [
      zstd_executable(),
      "-d",
      "-c",
      "-T%d" % max(1, int(num_threads)),
      "-q",
      *zstd_long_flags(),
      zst_path,
  ]
  return _tar_readable_via_decompress_tar_pipe(cmd, tar_bin)


@contextlib.contextmanager
def _open_tarfile_for_read(path, num_threads):
  """Open a tar or compressed daily archive for sequential reads."""
  if path.endswith(DAILY_ARCHIVE_ZST_SUFFIX) and shutil.which("zstd"):
    with zstd_decompress_stdout(
        path,
        num_threads,
    ) as stdout:
      with tarfile.open(fileobj=stdout, mode="r|") as tf:
        yield tf
  elif path.endswith(DAILY_ARCHIVE_GZ_SUFFIX) and zstd_gzip_supported():
    with zstd_gzip_decompress_stdout(path, num_threads) as stdout:
      with tarfile.open(fileobj=stdout, mode="r|") as tf:
        yield tf
  else:
    with tarfile.open(path, "r") as tf:
      yield tf


def _iter_tar_members(tf):
  """Yield tar members without forcing a full in-memory member list."""
  try:
    iterator = iter(tf)
  except TypeError:
    iterator = tf.getmembers()
  for member in iterator:
    yield member


def verify_tar_archive_readable(tar_path):
  """Return True if ``tar_path`` is a readable archive (full scan via ``tar tf``).

  For ``.tar.zst`` / ``.tar.gz``, uses ``zstd -d -c | tar tf -`` when zstd is
  available; otherwise ``tar tf`` on the file (or :mod:`tarfile` if ``tar`` is
  missing).
  """
  if not os.path.isfile(tar_path):
    return False
  tar_bin = _tar_list_executable()
  try:
    with file_read_lock_wait(tar_path):
      if tar_path.endswith(DAILY_ARCHIVE_ZST_SUFFIX) and shutil.which("zstd"):
        return _tar_zst_readable_via_zstd_tar_pipe(
            tar_path, tar_bin, archive_zstd_thread_count)
      if tar_path.endswith(DAILY_ARCHIVE_GZ_SUFFIX) and zstd_gzip_supported():
        return _tar_gz_readable_via_zstd_gzip_tar_pipe(
            tar_path, tar_bin, archive_zstd_thread_count)
      result = subprocess.run(
          [tar_bin, "tf", tar_path],
          capture_output=True,
          text=True,
          check=False,
      )
      return result.returncode == 0
  except FileNotFoundError:
    pass
  except (OSError, subprocess.SubprocessError):
    return False
  try:
    with file_read_lock_wait(tar_path):
      with tarfile.open(tar_path, "r") as tf:
        for _member in _iter_tar_members(tf):
          pass
    return True
  except (tarfile.TarError, OSError, EOFError):
    return False


def get_file_member_sizes_from_gzip_archive(gz_path):
  """File member name -> size (max if duplicated), reading **only** ``.tar.gz``.

  Does not open the sibling ``.tar``; used to compare sealed gzip to uncompressed tar.
  """
  if not gz_path.endswith(".tar.gz") or not os.path.isfile(gz_path):
    return {}
  _readable, members = _scan_gzip_archive_members_and_readable(gz_path)
  return members


def _archive_file_identity(path):
  """Return ``(mtime_ns, size)`` for cache keying, or ``None`` if missing."""
  if not os.path.isfile(path):
    return None
  st = os.stat(path)
  return (int(st.st_mtime_ns), int(st.st_size))


def _build_archive_validation_cache_key(compressed_path):
  tar_path = daily_tar_path_from_compressed(compressed_path)
  return (
      compressed_path,
      _archive_file_identity(compressed_path),
      _archive_file_identity(tar_path),
  )


def _scan_compressed_archive_members_and_readable(compressed_path):
  """Return ``(readable, members)`` from one streamed zstd/gzip pass."""
  if detect_compressed_format(compressed_path) is None or not os.path.isfile(
      compressed_path,
  ):
    return False, {}
  try:
    with file_read_lock_wait(compressed_path):
      with _open_tarfile_for_read(
          compressed_path, archive_zstd_thread_count,
      ) as tf:
        by_name = defaultdict(list)
        for m in _iter_tar_members(tf):
          if m.isfile():
            by_name[m.name].append(m.size)
        return True, {name: max(sizes) for name, sizes in by_name.items()}
  except Exception:
    return False, {}


def _scan_gzip_archive_members_and_readable(gz_path):
  """Return ``(readable, members)`` from one streamed gzip pass."""
  return _scan_compressed_archive_members_and_readable(gz_path)


def validate_sealed_daily_archive_for_raw_removal(
    archive_compressed_path,
    log_fn=log_print,
    *,
    validation_cache=None,
):
  """Validate uncompressed tar (if present) then sealed ``.tar.zst`` (or legacy ``.tar.gz``).

  Order: (1) readable ``YYYY-MM-DD.tar`` and member sizes if it exists;
  (2) readable sealed archive and member sizes from compressed file only;
  (3) if both exist, dicts must be equal. Returns ``(True, members)`` for use
  with ``get_verified_files_to_remove``, or ``(False, None)``.
  """
  fmt = detect_compressed_format(archive_compressed_path)
  if fmt not in ("zst", "gz"):
    if log_fn:
      log_fn(
          "Skipping removal validation: not a daily compressed archive: %s"
          % archive_compressed_path,
          flush=True,
      )
    return False, None
  sealed_path = archive_compressed_path
  tar_path = daily_tar_path_from_compressed(sealed_path)
  cache_key = None
  if validation_cache is not None:
    cache_key = _build_archive_validation_cache_key(sealed_path)
    cached = validation_cache.get(cache_key)
    if cached is not None:
      validation_cache["hits"] = int(validation_cache.get("hits", 0)) + 1
      cached_members = dict(cached["members"]) if cached["members"] is not None else None
      return bool(cached["ok"]), cached_members
    validation_cache["misses"] = int(validation_cache.get("misses", 0)) + 1
  members_tar = None
  if os.path.isfile(tar_path):
    if not verify_tar_archive_readable(tar_path):
      if log_fn:
        log_fn(
            "Skipping removal: uncompressed tar failed check: %s" % tar_path,
            flush=True,
        )
      return False, None
    members_tar = get_existing_archive_members(tar_path)
    if not members_tar:
      if log_fn:
        log_fn(
            "Skipping removal: uncompressed tar has no file members: %s" % tar_path,
            flush=True,
        )
      return False, None

  zst_path, _gz_path = compressed_sibling_paths(tar_path)
  if not os.path.isfile(sealed_path):
    if members_tar is None:
      if log_fn:
        log_fn(
            "Skipping removal: sealed archive missing: %s" % sealed_path,
            flush=True,
        )
      return False, None
    if log_fn:
      log_fn(
          "Sealed archive missing; creating from valid uncompressed tar: %s"
          % tar_path,
          flush=True,
      )
    try:
      atomic_seal_tar_to_zst(
          tar_path,
          zst_path,
          num_threads=archive_zstd_thread_count,
          compress_level=cfg.get_archive_zstd_level(),
          keep_uncompressed_tar=cfg.get_archive_keep_uncompressed_tar(),
          log_fn=log_fn,
      )
      sealed_path = zst_path
      sealed_readable, members_sealed = _scan_compressed_archive_members_and_readable(
          sealed_path,
      )
      if not sealed_readable or members_sealed != members_tar:
        if log_fn:
          log_fn(
              "Skipping removal: auto-seal member mismatch for %s "
              "(tar count=%s sealed count=%s)"
              % (sealed_path, len(members_tar), len(members_sealed)),
              flush=True,
          )
        return False, None
    except (OSError, subprocess.CalledProcessError) as exc:
      if log_fn:
        log_fn(
            "Skipping removal: failed to seal tar into zstd for %s (%s)"
            % (tar_path, exc),
            flush=True,
        )
      return False, None

  sealed_readable, members_sealed = _scan_compressed_archive_members_and_readable(
      sealed_path,
  )
  if not sealed_readable:
    if log_fn:
      log_fn(
          "Skipping removal: sealed archive failed integrity check: %s"
          % sealed_path,
          flush=True,
      )
    result = (False, None)
    if validation_cache is not None:
      validation_cache[cache_key] = {"ok": result[0], "members": result[1]}
    return result

  if members_tar is not None:
    if members_tar != members_sealed:
      if log_fn:
        log_fn(
            "Skipping removal: tar vs sealed member mismatch for %s "
            "(uncompressed count=%s sealed count=%s)"
            % (sealed_path, len(members_tar), len(members_sealed)),
            flush=True,
        )
      result = (False, None)
      if validation_cache is not None:
        validation_cache[cache_key] = {"ok": result[0], "members": result[1]}
      return result
    result = (True, members_tar)
    if validation_cache is not None:
      validation_cache[cache_key] = {"ok": result[0], "members": dict(result[1])}
    return result

  result = (True, members_sealed)
  if validation_cache is not None:
    validation_cache[cache_key] = {"ok": result[0], "members": dict(result[1])}
  return result


def replace_corrupt_tar_from_compressed_backup(
    tar_path,
    zst_path,
    gz_path,
    zstd_threads,
):
  """Remove corrupt ``tar_path``, then restore from ``.zst`` or legacy ``.gz``.

  Returns True if the filesystem is in a consistent state for the caller to
  append: either ``tar_path`` exists (restored from backup) or both backups
  and tar are absent. Returns False only if restore was attempted but
  ``tar_path`` is still missing afterward.
  """
  try:
    with file_write_lock(tar_path):
      if os.path.isfile(tar_path):
        os.remove(tar_path)
  except OSError:
    return False
  backup_path = zst_path if os.path.isfile(zst_path) else gz_path
  if not backup_path or not os.path.isfile(backup_path):
    return True
  return decompress_compressed_to_tar(
      backup_path,
      tar_path,
      zstd_threads,
  )


def replace_corrupt_tar_from_gzip_backup(tar_path, gz_path, zstd_threads):
  """Deprecated wrapper: prefer :func:`replace_corrupt_tar_from_compressed_backup`."""
  zst_path, _legacy_gz = compressed_sibling_paths(tar_path)
  return replace_corrupt_tar_from_compressed_backup(
      tar_path, zst_path, gz_path, zstd_threads,
  )


def get_existing_archive_members(tar_path):
  """Read tar at tar_path and return dict of member name -> size for **file** members.

  If the same path appears multiple times (e.g. repeated append passes), the
  reported size is the **largest** among those entries so verification matches
  the preferred retained copy after deduplication.
  """
  if not os.path.exists(tar_path):
    return {}
  try:
    with file_read_lock_wait(tar_path):
      with _open_tarfile_for_read(tar_path, archive_zstd_thread_count) as tf:
        by_name = defaultdict(list)
        for m in _iter_tar_members(tf):
          if m.isfile():
            by_name[m.name].append(m.size)
        return {name: max(sizes) for name, sizes in by_name.items()}
  except Exception:
    return {}
  finally:
    _remove_read_lock_sidecar(tar_path)


def get_existing_archive_members_for_daily_archive(archive_compressed_path):
  """File member sizes for a daily ``.tar.zst`` or legacy ``.tar.gz``.

  Prefers sibling ``.tar`` when present (mutable / post-append). Otherwise
  reads members directly from the sealed archive.
  """
  if detect_compressed_format(archive_compressed_path) is None:
    return {}
  tar_path = daily_tar_path_from_compressed(archive_compressed_path)
  if os.path.isfile(tar_path):
    return get_existing_archive_members(tar_path)
  if not os.path.isfile(archive_compressed_path):
    return {}
  try:
    with file_read_lock_wait(archive_compressed_path):
      with _open_tarfile_for_read(
          archive_compressed_path, archive_zstd_thread_count,
      ) as tf:
        by_name = defaultdict(list)
        for m in _iter_tar_members(tf):
          if m.isfile():
            by_name[m.name].append(m.size)
        return {name: max(sizes) for name, sizes in by_name.items()}
  except Exception:
    return {}


def remove_verified_archived_raw_files(
    archive_data_dir,
    host_name_ext,
    tgz_archive_dir,
    *,
    log_fn=log_print,
    archive_stats_files_fn=None,
    ingest_ready_fn=None,
):
  """Remove raw stats files only after tar + sealed archive validation.

  For each daily ``.tar.zst`` (or legacy ``.tar.gz``), requires: sibling ``.tar`` (if present) passes
  ``verify_tar_archive_readable`` and yields file member sizes; ``.tar.gz`` passes
  the same and yields sizes from the gzip archive only; the two maps must match.
  Then deletes raw paths that match a member name and size.

  When ``ingest_ready_fn`` is set (production: head timestamp in ``host_data``),
  bootstrap and removal apply only to paths for which it returns true.

  Scans all closed segments under ``archive_data_dir`` (same rules as ingest).
  Run after ``seal_dirty_daily_archives``. If only ``.tar.gz`` exists (no sibling
  ``.tar``), gzip alone is validated and its map is used.

  If raw files map to a day but neither ``.tar`` nor ``.tar.gz`` exists yet
  (e.g. ingest already finished and archival was never run for those paths),
  this routine bootstraps the daily archive via ``archive_stats_files`` before
  validation and removal.
  """
  def _path_ingest_ready(path):
    if ingest_ready_fn is None:
      return True
    return bool(ingest_ready_fn(path))

  paths = collect_stats_files_in_range(
      archive_data_dir, "all", None, host_name_ext)
  if not paths:
    return
  mapping = build_archive_mapping(paths, tgz_archive_dir)
  if archive_stats_files_fn is None:
    import hpcperfstats.dbload.sync_timedb as _sync_timedb_mod

    archive_stats_files_fn = _sync_timedb_mod.archive_stats_files
  validation_cache = {"hits": 0, "misses": 0}
  validation_targets = []
  for archive_path, stats_paths in mapping.items():
    if detect_compressed_format(archive_path) is not None:
      tar_path = daily_tar_path_from_compressed(archive_path)
      bootstrap_ready = [
          p for p in stats_paths if _path_ingest_ready(p)
      ]
      if (not os.path.isfile(archive_path) and not os.path.isfile(tar_path)
          and bootstrap_ready):
        if log_fn:
          log_fn(
              "Bootstrapping missing daily archive from %d raw stats file(s): %s"
              % (len(bootstrap_ready), archive_path),
              flush=True,
          )
        if not archive_stats_files_fn((archive_path, list(bootstrap_ready))):
          if log_fn:
            log_fn(
                "Skipping removal: could not bootstrap daily archive: %s"
                % archive_path,
                flush=True,
            )
          continue
      elif (not os.path.isfile(archive_path) and not os.path.isfile(tar_path)
            and stats_paths and not bootstrap_ready and log_fn):
        log_fn(
            "Skipping bootstrap for %s: %d path(s) without head timestamp in DB"
            % (archive_path, len(stats_paths)),
            flush=True,
        )
    validation_targets.append((archive_path, list(stats_paths)))

  if validation_targets:
    workers = _get_archive_validation_worker_count(len(validation_targets))
    validation_cache["misses"] += len(validation_targets)
    validation_started = time.time()
    success_count = 0
    failed_count = 0
    stats_paths_by_gz = {gz_path: stats_paths for gz_path, stats_paths in validation_targets}
  else:
    workers = 1
    validation_started = time.time()
    success_count = 0
    failed_count = 0
    stats_paths_by_gz = {}

  for gz_path, ok, members in _iter_archive_validation_results_stream(
      [gz_path for gz_path, _stats_paths in validation_targets],
      log_fn=log_fn,
      validation_cache=validation_cache if workers <= 1 else None,
  ):
    if not ok or members is None:
      failed_count += 1
      continue
    success_count += 1
    for stats_path in stats_paths_by_gz.get(gz_path, []):
      for path in get_verified_files_to_remove([stats_path], members):
        if not _path_ingest_ready(path):
          continue
        if log_fn:
          log_fn(
              "removing stats file (scheduled archive maintenance): " + path,
              flush=True,
          )
        try:
          with file_write_lock(path):
            os.remove(path)
        except OSError as exc:
          if log_fn:
            log_fn("Could not remove %s: %s" % (path, exc), flush=True)
  _log_archive_validation_summary(
      log_fn=log_fn,
      validation_targets_count=len(validation_targets),
      workers=workers,
      success_count=success_count,
      failed_count=failed_count,
      validation_started=validation_started,
      validation_cache=validation_cache,
  )


def _normalize_daily_gz_path(path):
  """Deprecated: use :func:`normalize_daily_compressed_path`."""
  return normalize_daily_compressed_path(path)


def build_remaining_raw_stats_by_daily_gz(
    archive_data_dir,
    host_name_ext,
    tgz_archive_dir,
):
  """Map each daily ``.tar.zst`` to closed raw stats paths still on disk for that day.

  Uses the same discovery and first-timestamp grouping as archival
  (``collect_stats_files_in_range`` + ``build_archive_mapping``). Not filtered by
  DB head-ingest readiness: not-yet-ingested closed segments still block ``.tar``
  removal (see ``sync-timedb-db-before-archive-contract.mdc``).

  Files with no parseable first timestamp are omitted from the mapping and do not
  block removal (same as archival bootstrap).
  """
  paths = collect_stats_files_in_range(
      archive_data_dir, "all", None, host_name_ext)
  if not paths:
    return {}
  mapping = build_archive_mapping(paths, tgz_archive_dir)
  return {
      normalize_daily_compressed_path(archive_path): list(stats_paths)
      for archive_path, stats_paths in mapping.items()
      if detect_compressed_format(archive_path) is not None and stats_paths
  }


def daily_gz_has_remaining_raw_stats(gz_path, remaining_by_gz):
  """True if ``remaining_by_gz`` lists any raw stats path for this daily archive."""
  if not remaining_by_gz:
    return False
  key = _normalize_daily_gz_path(gz_path)
  return bool(remaining_by_gz.get(key))


def remove_verified_uncompressed_daily_tars(
    daily_archive_dir,
    *,
    log_fn=log_print,
    remaining_raw_by_gz=None,
    force_remove_uncompressed_tar=False,
):
  """Remove ``YYYY-MM-DD.tar`` only after tar/tar.gz verification succeeds.

  Called after ``remove_verified_archived_raw_files`` on every archive maintenance
  pass. Skips a day when ``remaining_raw_by_gz`` still lists closed raw stats for
  that calendar day (filesystem gate; not the DB head-ingest gate), unless
  ``force_remove_uncompressed_tar`` is true (startup maintenance only).

  When ``archive_keep_uncompressed_tar`` is false, ``atomic_seal_tar_to_zst`` may
  also unlink the ``.tar`` after sealing when no raw stats remain for that day.
  """
  if not daily_archive_dir or not os.path.isdir(daily_archive_dir):
    return
  validation_cache = {"hits": 0, "misses": 0}
  validation_targets = []
  tar_by_gz = {}
  for tar_path in sorted(iter_daily_tar_paths(daily_archive_dir)):
    zst_path, _gz_path = compressed_sibling_paths(tar_path)
    if not os.path.isfile(tar_path):
      continue
    sealed_path = zst_path if os.path.isfile(zst_path) else (
        _gz_path if os.path.isfile(_gz_path) else zst_path
    )
    validation_targets.append(sealed_path)
    tar_by_gz[sealed_path] = tar_path

  if validation_targets:
    workers = _get_archive_validation_worker_count(len(validation_targets))
    validation_cache["misses"] += len(validation_targets)
    validation_started = time.time()
    success_count = 0
    failed_count = 0
  else:
    workers = 1
    validation_started = time.time()
    success_count = 0
    failed_count = 0

  for gz_path, ok, members in _iter_archive_validation_results_stream(
      validation_targets,
      log_fn=log_fn,
      validation_cache=validation_cache if workers <= 1 else None,
  ):
    if not ok or members is None:
      failed_count += 1
      continue
    success_count += 1
    tar_path = tar_by_gz[gz_path]
    if (
        not force_remove_uncompressed_tar
        and daily_gz_has_remaining_raw_stats(gz_path, remaining_raw_by_gz)
    ):
      if log_fn:
        log_fn(
            "Skipping removal of verified uncompressed tar (raw stats still "
            "present for day): %s" % tar_path,
            flush=True,
        )
      continue
    try:
      with file_write_lock(tar_path):
        if os.path.isfile(tar_path):
          os.remove(tar_path)
      if log_fn:
        log_fn(
            "Maintenance removed verified uncompressed tar: %s" % tar_path,
            flush=True,
        )
    except OSError as exc:
      if log_fn:
        log_fn("Could not remove verified tar %s: %s" % (tar_path, exc), flush=True)
  _log_archive_validation_summary(
      log_fn=log_fn,
      validation_targets_count=len(validation_targets),
      workers=workers,
      success_count=success_count,
      failed_count=failed_count,
      validation_started=validation_started,
      validation_cache=validation_cache,
  )


def tar_has_duplicate_file_members(tar_path):
  """Return True if any file member path appears more than once in archive order."""
  if not os.path.isfile(tar_path):
    return False
  try:
    with file_read_lock_wait(tar_path):
      with tarfile.open(tar_path, "r") as tf:
        seen = set()
        for m in _iter_tar_members(tf):
          if not m.isfile():
            continue
          if m.name in seen:
            return True
          seen.add(m.name)
        return False
  except Exception:
    return False


def _dedupe_member_indices_keep_largest_file_per_name(members):
  """Indices to keep: all non-file members; for each file name, one entry with max size (tie: last)."""
  keep = set()
  by_name = defaultdict(list)
  for i, m in enumerate(members):
    if m.isfile():
      by_name[m.name].append((i, m.size))
    else:
      keep.add(i)
  for _name, lst in by_name.items():
    max_sz = max(s for _i, s in lst)
    tie_indices = [i for i, s in lst if s == max_sz]
    keep.add(tie_indices[-1])
  return keep


def dedupe_tar_keep_largest_file_per_member(tar_path, log_fn=log_print):
  """Rewrite ``tar_path`` so each file path appears once: keep largest size (tie: last).

  Writes ``tar_path`` + ``.dedupe.tmp``, verifies, then ``os.replace``. Returns
  False on failure (original tar unchanged if replace never ran).
  """
  if not os.path.isfile(tar_path):
    return True
  tmp_path = "%s.dedupe.tmp" % tar_path
  try:
    if os.path.exists(tmp_path):
      os.remove(tmp_path)
  except OSError:
    pass
  try:
    with file_write_lock(tar_path):
      file_keep = {}
      with tarfile.open(tar_path, "r") as tin:
        for idx, member in enumerate(_iter_tar_members(tin)):
          if not member.isfile():
            continue
          prev = file_keep.get(member.name)
          if prev is None or member.size > prev[0] or (
              member.size == prev[0] and idx > prev[1]
          ):
            file_keep[member.name] = (member.size, idx)
      with tarfile.open(tar_path, "r") as tin:
        with tarfile.open(tmp_path, "w") as tout:
          for idx, member in enumerate(_iter_tar_members(tin)):
            if member.isfile():
              keep = file_keep.get(member.name)
              if keep is None or keep[1] != idx:
                continue
              fobj = tin.extractfile(member)
              if fobj is None:
                continue
              try:
                tout.addfile(member, fobj)
              finally:
                fobj.close()
              continue
            tout.addfile(member)
      if not verify_tar_archive_readable(tmp_path):
        try:
          os.remove(tmp_path)
        except OSError:
          pass
        return False
      os.replace(tmp_path, tar_path)
    if log_fn:
      log_fn("Deduplicated archive (largest wins per path): %s" % tar_path, flush=True)
    return True
  except Exception:
    try:
      if os.path.exists(tmp_path):
        os.remove(tmp_path)
    except OSError:
      pass
    return False


def resolve_preferred_archive_path_for_read(path):
  """Prefer mutable uncompressed ``.tar`` when it and a compressed sibling exist."""
  tar_path = daily_tar_path_from_compressed(path)
  if os.path.isfile(tar_path):
    return tar_path
  return path


def _compressed_backup_and_uncompressed_targets(open_path):
  """Return ``(zst_path, gz_path, uncompressed_tar_path)`` for restore."""
  tar_path = daily_tar_path_from_compressed(open_path)
  zst_path, gz_path = compressed_sibling_paths(tar_path)
  return (zst_path, gz_path, tar_path)


def _gzip_backup_and_uncompressed_targets(open_path):
  """Deprecated: use :func:`_compressed_backup_and_uncompressed_targets`."""
  zst_path, gz_path, tar_path = _compressed_backup_and_uncompressed_targets(
      open_path,
  )
  backup = zst_path if os.path.isfile(zst_path) else gz_path
  return (backup, tar_path)


def iter_tar_file_tasks(tar_path):
  """Yield ``(tar_path, member_name)`` for file members only (no dirs).

  If the tar is unreadable and a sibling ``.tar.zst`` / ``.tar.gz`` exists, delete the
  unreadable tar, restore from compressed backup, and retry once.

  When both ``.tar`` and a compressed sibling exist (deferred compression), the
  uncompressed ``.tar`` is used so readers see the latest appends.
  """
  open_path = resolve_preferred_archive_path_for_read(tar_path)

  def _iter_members():
    with file_read_lock_wait(open_path):
      with _open_tarfile_for_read(open_path, archive_zstd_thread_count) as archive_tar:
        try:
          member_infos = iter(archive_tar)
        except TypeError:
          member_infos = archive_tar.getmembers()
        for member_info in member_infos:
          if not member_info.isfile():
            continue
          yield (open_path, member_info.name)

  def _restore_from_compressed():
    zst_path, gz_path, tar_out = _compressed_backup_and_uncompressed_targets(
        open_path,
    )
    backup_path = zst_path if os.path.isfile(zst_path) else gz_path
    if not backup_path or not os.path.exists(backup_path):
      return False
    try:
      if os.path.exists(tar_out):
        os.remove(tar_out)
    except OSError:
      return False
    return decompress_compressed_to_tar(
        backup_path,
        tar_out,
        archive_zstd_thread_count,
    )

  try:
    yield from _iter_members()
  except (tarfile.TarError, OSError, EOFError):
    log_print(
        "Unable to read archive %s (possible corruption); attempting restore "
        "from compressed backup" % open_path
    )
    if not _restore_from_compressed():
      log_print(
          "Archive recovery failed for %s; no usable compressed backup"
          % open_path
      )
      raise
    log_print("Archive recovery succeeded for %s; retrying read" % open_path)
    open_path = resolve_preferred_archive_path_for_read(tar_path)
  yield from _iter_members()


def get_tar_file_tasks(tar_path):
  """Return list of (tar_path, member_name) for file members only (no dirs).

  Wrapper over ``iter_tar_file_tasks`` for callers/tests that need eager lists.
  """
  return list(iter_tar_file_tasks(tar_path))


def parse_archive_date_from_daily_tar_path(tar_path):
  """Return date from ``YYYY-MM-DD.tar`` basename, or None if not matched."""
  base = os.path.basename(tar_path)
  if not _DAILY_TAR_BASENAME_RE.match(base):
    return None
  try:
    return datetime.strptime(base[:10], "%Y-%m-%d").date()
  except ValueError:
    return None


def is_daily_tar_sealed_dirty(tar_path, zst_path, gz_path):
  """True if ``.tar`` should be re-sealed to ``.tar.zst`` (zst missing/stale or only legacy gz)."""
  if not os.path.isfile(tar_path):
    return False
  if os.path.isfile(zst_path):
    try:
      return os.path.getmtime(tar_path) > os.path.getmtime(zst_path)
    except OSError:
      return True
  if os.path.isfile(gz_path) and not os.path.isfile(zst_path):
    return True
  if not os.path.exists(zst_path):
    return True
  try:
    return os.path.getmtime(tar_path) > os.path.getmtime(zst_path)
  except OSError:
    return True


def is_daily_tar_gz_dirty(tar_path, gz_path):
  """Deprecated: use :func:`is_daily_tar_sealed_dirty`."""
  zst_path, legacy_gz = compressed_sibling_paths(tar_path)
  return is_daily_tar_sealed_dirty(tar_path, zst_path, legacy_gz)


def should_seal_daily_tar(
    tar_path,
    zst_path,
    idle_seconds,
    today_local_date,
    seal_immediately_if_dirty=False,
    gz_path=None,
):
  """Whether to seal now: dirty tar/zst pair and idle / prior-day rules.

  If ``seal_immediately_if_dirty`` (e.g. end of a sync_timedb ingest pass), any
  dirty pair is sealed regardless of idle. Otherwise today's archive waits until
  ``idle_seconds`` after its last mtime (prior calendar days seal when dirty).
  """
  if gz_path is None:
    _zst, gz_path = compressed_sibling_paths(tar_path)
  if not is_daily_tar_sealed_dirty(tar_path, zst_path, gz_path):
    return False
  if seal_immediately_if_dirty:
    return True
  archive_date = parse_archive_date_from_daily_tar_path(tar_path)
  tar_mtime_age = time.time() - os.path.getmtime(tar_path)
  if archive_date is not None and archive_date < today_local_date:
    return True
  return tar_mtime_age >= float(idle_seconds)


def iter_daily_tar_paths(daily_archive_dir):
  """Yield paths to ``YYYY-MM-DD.tar`` under ``daily_archive_dir``."""
  try:
    names = os.listdir(daily_archive_dir)
  except OSError:
    return
  for name in names:
    if _DAILY_TAR_BASENAME_RE.match(name):
      yield os.path.join(daily_archive_dir, name)


def compare_compressed_archive_members(gz_path, zst_path):
  """Return ``(equal, gz_members, zst_members)`` for migration checks."""
  gz_ok, gz_members = _scan_compressed_archive_members_and_readable(gz_path)
  zst_ok, zst_members = _scan_compressed_archive_members_and_readable(zst_path)
  if not gz_ok or not zst_ok:
    return False, gz_members, zst_members
  return archive_member_maps_equivalent(gz_members, zst_members), gz_members, zst_members


def drop_legacy_gz_if_equivalent_to_zst(gz_path, zst_path, log_fn=log_print):
  """Remove legacy ``.tar.gz`` when member maps match ``.tar.zst``."""
  if not os.path.isfile(gz_path) or not os.path.isfile(zst_path):
    return
  equal, gz_members, zst_members = compare_compressed_archive_members(
      gz_path, zst_path,
  )
  if (
      equal
      and sum_member_bytes(gz_members) == sum_member_bytes(zst_members)
  ):
    try:
      with file_write_lock(gz_path):
        os.remove(gz_path)
      if log_fn:
        log_fn(
            "Removed legacy gzip archive after zst equivalence: %s" % gz_path,
            flush=True,
        )
    except OSError as exc:
      if log_fn:
        log_fn("Could not remove legacy gzip %s: %s" % (gz_path, exc), flush=True)
    return
  if log_fn:
    log_fn(
        "Keeping legacy gzip %s: member mismatch with %s "
        "(gzip_bytes=%s zst_bytes=%s gzip_members=%s zst_members=%s)"
        % (
            gz_path,
            zst_path,
            sum_member_bytes(gz_members),
            sum_member_bytes(zst_members),
            len(gz_members),
            len(zst_members),
        ),
        flush=True,
    )


def atomic_seal_tar_to_zst(
    tar_path,
    zst_path,
    num_threads,
    compress_level,
    keep_uncompressed_tar,
    log_fn=log_print,
    remaining_raw_by_gz=None,
    force_remove_uncompressed_tar=False,
):
  """Compress ``tar_path`` to ``zst_path`` using temp file, ``zstd -t``, ``os.replace``."""
  if not os.path.isfile(tar_path):
    return
  if not shutil.which("zstd"):
    raise RuntimeError("zstd executable not found on PATH")
  tmp_zst = "%s.tmp" % zst_path
  try:
    if os.path.exists(tmp_zst):
      os.remove(tmp_zst)
  except OSError:
    pass
  try:
    with file_write_lock(tar_path):
      if os.path.isfile(zst_path):
        existing_ok, existing_members = _scan_compressed_archive_members_and_readable(
            zst_path,
        )
        if existing_ok:
          tar_members = get_existing_archive_members(tar_path)
          if len(existing_members) > len(tar_members):
            if log_fn:
              log_fn(
                  "Seal refused: existing zst has more members than tar "
                  "(zst=%s tar=%s): %s"
                  % (len(existing_members), len(tar_members), zst_path),
                  flush=True,
              )
            return
          if sum_member_bytes(existing_members) > sum_member_bytes(tar_members):
            if log_fn:
              log_fn(
                  "Seal refused: existing zst byte sum exceeds tar "
                  "(zst=%s tar=%s): %s"
                  % (
                      sum_member_bytes(existing_members),
                      sum_member_bytes(tar_members),
                      zst_path,
                  ),
                  flush=True,
              )
            return
      zstd_compress_tar_to_file(
          tar_path,
          tmp_zst,
          num_threads,
          compress_level,
      )
      zstd_test(tmp_zst, num_threads)
      with file_write_lock(zst_path):
        os.replace(tmp_zst, zst_path)
  except BaseException:
    try:
      if os.path.exists(tmp_zst):
        os.remove(tmp_zst)
    except OSError:
      pass
    raise
  if log_fn:
    log_fn("Sealed archive %s -> %s" % (tar_path, zst_path), flush=True)
  zst_key = normalize_daily_compressed_path(zst_path)
  if keep_uncompressed_tar:
    if log_fn:
      log_fn("Sealed archive retaining uncompressed tar: %s" % tar_path, flush=True)
  elif (
      not force_remove_uncompressed_tar
      and daily_gz_has_remaining_raw_stats(zst_key, remaining_raw_by_gz)
  ):
    if log_fn:
      log_fn(
          "Sealed archive retaining uncompressed tar (raw stats still present "
          "for day): %s" % tar_path,
          flush=True,
      )
  else:
    try:
      with file_write_lock(tar_path):
        os.remove(tar_path)
      if log_fn:
        log_fn("Sealed archive removed uncompressed tar: %s" % tar_path, flush=True)
    except OSError:
      pass


def atomic_seal_tar_to_gz(
    tar_path,
    gz_path,
    num_threads,
    compress_level,
    keep_uncompressed_tar,
    log_fn=log_print,
    remaining_raw_by_gz=None,
):
  """Deprecated: seals to ``.tar.zst`` at canonical sibling path."""
  zst_path, _legacy_gz = compressed_sibling_paths(tar_path)
  return atomic_seal_tar_to_zst(
      tar_path,
      zst_path,
      num_threads,
      cfg.get_archive_zstd_level(),
      keep_uncompressed_tar,
      log_fn=log_fn,
      remaining_raw_by_gz=remaining_raw_by_gz,
  )


def seal_dirty_daily_archives(
    daily_archive_dir,
    *,
    local_tz,
    zstd_threads,
    compress_level,
    keep_uncompressed_tar,
    idle_seconds,
    seal_immediately_if_dirty=False,
    log_fn=log_print,
    remaining_raw_by_gz=None,
    force_remove_uncompressed_tar=False,
):
  """Seal every dirty ``YYYY-MM-DD.tar`` under ``daily_archive_dir`` per policy."""
  if not daily_archive_dir or not os.path.isdir(daily_archive_dir):
    return
  today_local = datetime.now(local_tz).date()
  for tar_path in sorted(iter_daily_tar_paths(daily_archive_dir)):
    zst_path, gz_path = compressed_sibling_paths(tar_path)
    if not should_seal_daily_tar(
        tar_path,
        zst_path,
        idle_seconds,
        today_local,
        seal_immediately_if_dirty=seal_immediately_if_dirty,
        gz_path=gz_path,
    ):
      continue
    try:
      atomic_seal_tar_to_zst(
          tar_path,
          zst_path,
          zstd_threads,
          compress_level,
          keep_uncompressed_tar,
          log_fn=log_fn,
          remaining_raw_by_gz=remaining_raw_by_gz,
          force_remove_uncompressed_tar=force_remove_uncompressed_tar,
      )
      drop_legacy_gz_if_equivalent_to_zst(gz_path, zst_path, log_fn=log_fn)
    except (subprocess.CalledProcessError, RuntimeError) as exc:
      if log_fn:
        log_fn("Seal failed for %s: %s" % (tar_path, exc), flush=True)


def filter_files_to_add_to_archive(stats_files, existing_members, debug=False):
  """Return list of stats file paths that are not already in archive with same size."""
  to_add = []
  for path in stats_files:
    member_name = get_tar_member_name(path)
    if member_name not in existing_members:
      to_add.append(path)
      continue
    try:
      file_size = os.path.getsize(path)
    except OSError:
      to_add.append(path)
      continue
    if file_size != existing_members[member_name]:
      to_add.append(path)
    elif debug:
      log_print("file %s found in archive, skipping" % path)
  return to_add


def get_verified_files_to_remove(stats_files, existing_members):
  """Return list of stats file paths that exist in archive with same size (safe to remove)."""
  to_remove = []
  for path in stats_files:
    member_name = get_tar_member_name(path)
    if member_name not in existing_members:
      continue
    try:
      if os.path.getsize(path) == existing_members[member_name]:
        to_remove.append(path)
    except OSError:
      pass
  return to_remove


def get_stats_chunk(stats_files, chunk_index, chunk_size):
  """Return slice of stats_files for chunk chunk_index (0-based)."""
  start = chunk_index * chunk_size
  end = (chunk_index + 1) * chunk_size
  return stats_files[start:end]


def stats_file_is_active_segment(stats_path):
  """Return True if ``stats_path`` is still being appended by listend.

  On ``$`` rotation, listend creates a new ``current`` and hard-links it to an
  epoch-named file; both names refer to the same inode until the next ``$``,
  when ``current`` is unlinked from that inode. Only then is the epoch file a
  complete, stable segment. Same-inode-as-``current`` means still active.
  """
  host_dir = os.path.dirname(stats_path)
  current_path = os.path.join(host_dir, "current")
  try:
    if not os.path.isfile(current_path):
      return False
    return os.path.samefile(stats_path, current_path)
  except OSError:
    return False


def collect_stats_files_in_range(
    directory,
    startdate,
    enddate,
    host_name_ext,
    host_scan_hints=None,
    force_full_scan=False,
):
  """Scan ``archive_dir`` for stats files in immediate subdirs whose names end
  with ``host_name_ext`` (same value as ``DEFAULT.host_name_ext`` in ini).

  Skips the live segment: epoch files still hard-linked to ``current`` (same
  inode) are omitted so sync does not race with listend appends.

  When startdate is ``'all'``, every eligible file is returned (no date
  filtering). Otherwise files are included if mtime or filename epoch falls in
  (startdate - 1 day, enddate]. Returns paths sorted newest-first (by filename
  epoch when numeric, else mtime). If ``host_name_ext`` is empty after strip,
  returns an empty list.
  """
  stats_files = []
  suffix = (host_name_ext or "").strip()
  if not suffix:
    return []
  for entry in os.scandir(directory):
    if entry.is_file() or not entry.is_dir():
      continue
    if not entry.name.endswith(suffix):
      continue
    if isinstance(host_scan_hints, dict) and not force_full_scan:
      try:
        dir_mtime = int(entry.stat().st_mtime)
      except OSError:
        dir_mtime = -1
      prev_mtime = host_scan_hints.get(entry.path)
      host_scan_hints[entry.path] = dir_mtime
      if prev_mtime is not None and prev_mtime == dir_mtime:
        continue
    for stats_file in os.scandir(entry.path):
      if not stats_file.is_file() or stats_file.name.startswith("."):
        continue
      if _is_lock_file_name(stats_file.name):
        continue
      if stats_file.name.startswith("current"):
        continue
      if stats_file_is_active_segment(stats_file.path):
        continue
      try:
        st_info = stats_file.stat()
        st_mtime = int(st_info.st_mtime)
        fdate_mtime = datetime.fromtimestamp(st_mtime)
      except Exception as e:
        log_print("error in obtaining timestamp of raw data files: ", str(e))
        continue

      fdate_name = None
      try:
        fname_epoch = int(os.path.basename(stats_file.path))
        fdate_name = datetime.fromtimestamp(fname_epoch)
      except Exception:
        pass

      sort_epoch = None
      if fdate_name is not None:
        sort_epoch = int(os.path.basename(stats_file.path))
      else:
        sort_epoch = st_mtime

      if startdate == "all":
        stats_files.append((stats_file.path, sort_epoch))
        continue

      def _in_range(ts):
        if ts is None:
          return False
        return not (ts <= startdate - timedelta(days=1) or ts > enddate)

      in_range_mtime = _in_range(fdate_mtime)
      in_range_name = _in_range(fdate_name)
      if not (in_range_mtime or in_range_name):
        continue
      stats_files.append((stats_file.path, sort_epoch))

  # Sort by effective timestamp descending (newest files first); None values
  # sort last. Then return just the paths.
  stats_files.sort(key=lambda item: (item[1] is None, item[1]), reverse=True)
  return [path for path, _ in stats_files]


def rescan_pending_stats_files(
    directory,
    startdate,
    enddate,
    host_name_ext,
    processed_files,
    host_scan_hints=None,
    full_rescan_every=10,
):
  """Return newest-first files still pending after excluding processed files."""
  should_force_full = True
  if isinstance(host_scan_hints, dict):
    should_force_full = (
        int(host_scan_hints.get("__rescan_count__", 0)) % max(1, int(full_rescan_every)) == 0
    )
    host_scan_hints["__rescan_count__"] = int(
        host_scan_hints.get("__rescan_count__", 0)
    ) + 1
  discovered_files = collect_stats_files_in_range(
      directory,
      startdate,
      enddate,
      host_name_ext,
      host_scan_hints=host_scan_hints,
      force_full_scan=should_force_full,
  )
  processed_set = set(processed_files or [])
  return [path for path in discovered_files if path not in processed_set]


def build_archive_mapping(
    files_to_be_archived,
    tgz_archive_dir,
    parse_first_ts_fn=None,
    first_timestamp_by_path=None,
):
  """Group stats file paths by daily archive path (``YYYY-MM-DD.tar.zst``).

  Uses parse_first_ts_fn to get timestamp from each file. Files with no
  parseable timestamp are skipped. Today's files are included (closed
  segments only reach this list; active segments are filtered earlier).
  """
  if parse_first_ts_fn is None:
    parse_first_ts_fn = parse_first_timestamp_line
  ar_file_mapping = {}
  skipped_no_ts = 0
  for stats_fname in files_to_be_archived:
    precomputed_ts = None
    if first_timestamp_by_path:
      precomputed_ts = first_timestamp_by_path.get(stats_fname)
    if precomputed_ts is not None:
      t = precomputed_ts
      _jid = _host = None
    else:
      t = _read_first_timestamp_from_stats_file(stats_fname, parse_first_ts_fn)
    if t is None:
      log_print(
          "Unable to find first timestamp in %s, skipping archiving"
          % stats_fname
      )
      skipped_no_ts += 1
      continue
    file_date = datetime.fromtimestamp(float(t))
    archive_fname = daily_compressed_path_for_date(tgz_archive_dir, file_date)
    if archive_fname not in ar_file_mapping:
      ar_file_mapping[archive_fname] = []
    ar_file_mapping[archive_fname].append(stats_fname)
  if skipped_no_ts and not ar_file_mapping:
    log_print(
        "No files added to archive mapping (%d skipped: no timestamp)"
        % skipped_no_ts
    )
  return ar_file_mapping


def collect_first_timestamps_by_path(files_to_be_archived, parse_first_ts_fn=None):
  """Return {path: first_timestamp_str} for files with parseable first timestamp."""
  if parse_first_ts_fn is None:
    parse_first_ts_fn = parse_first_timestamp_line
  timestamps = {}
  for stats_fname in files_to_be_archived:
    t = _read_first_timestamp_from_stats_file(stats_fname, parse_first_ts_fn)
    if t is not None:
      timestamps[stats_fname] = t
  return timestamps
