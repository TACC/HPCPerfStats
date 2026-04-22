"""Pure helpers for sync_timedb archiving, tar utilities, and file discovery (no Django). Used by sync_timedb and by unit tests."""
import os
import re
import shutil
import subprocess
import tarfile
import time
from collections import defaultdict
from datetime import datetime, timedelta

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload.pigz_cli import pigz_decompress_verbose, pigz_executable
from hpcperfstats.dbload.sync_timedb_parsing import parse_first_timestamp_line
from hpcperfstats.file_locking import (
    LOCK_EXPIRY_SECONDS,
    LOCK_SUFFIX,
    file_read_lock_wait,
    file_write_lock,
)
from hpcperfstats.print_utils import log_print

pigz_thread_count = max(1, cfg.get_worker_thread_count(4))

_DAILY_TAR_BASENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.tar$")


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


def verify_tar_archive_readable(tar_path):
  """Return True if ``tar_path`` is a readable archive (full scan via ``tar tf``).

  Works for uncompressed ``.tar`` and ``.tar.gz`` (``tar tf`` detects compression).
  Falls back to :mod:`tarfile` header scan only if the ``tar`` binary is missing.
  """
  if not os.path.isfile(tar_path):
    return False
  tar_bin = _tar_list_executable()
  try:
    with file_read_lock_wait(tar_path):
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
        tf.getmembers()
    return True
  except (tarfile.TarError, OSError, EOFError):
    return False


def get_file_member_sizes_from_gzip_archive(gz_path):
  """File member name -> size (max if duplicated), reading **only** ``.tar.gz``.

  Does not open the sibling ``.tar``; used to compare sealed gzip to uncompressed tar.
  """
  if not gz_path.endswith(".tar.gz") or not os.path.isfile(gz_path):
    return {}
  try:
    with file_read_lock_wait(gz_path):
      with tarfile.open(gz_path, "r") as tf:
        by_name = defaultdict(list)
        for m in tf.getmembers():
          if m.isfile():
            by_name[m.name].append(m.size)
        return {name: max(sizes) for name, sizes in by_name.items()}
  except Exception:
    return {}


def validate_sealed_daily_archive_for_raw_removal(archive_gz_path, log_fn=log_print):
  """Validate uncompressed tar (if present) then ``.tar.gz``; member lists must match.

  Order: (1) readable ``YYYY-MM-DD.tar`` and member sizes if it exists;
  (2) readable ``YYYY-MM-DD.tar.gz`` and member sizes from gzip only;
  (3) if both exist, dicts must be equal. Returns ``(True, members)`` for use
  with ``get_verified_files_to_remove``, or ``(False, None)``.
  """
  if not archive_gz_path.endswith(".tar.gz"):
    if log_fn:
      log_fn(
          "Skipping removal validation: not a .tar.gz path: %s" % archive_gz_path,
          flush=True,
      )
    return False, None
  gz_path = archive_gz_path
  tar_path = gz_path[:-len(".gz")]
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

  if not os.path.isfile(gz_path):
    if members_tar is None:
      if log_fn:
        log_fn("Skipping removal: sealed gzip missing: %s" % gz_path, flush=True)
      return False, None
    if log_fn:
      log_fn(
          "Sealed gzip missing; creating from valid uncompressed tar: %s"
          % tar_path,
          flush=True,
      )
    try:
      atomic_seal_tar_to_gz(
          tar_path,
          gz_path,
          num_threads=pigz_thread_count,
          compress_level=cfg.get_archive_pigz_level(),
          keep_uncompressed_tar=cfg.get_archive_keep_uncompressed_tar(),
          log_fn=log_fn,
      )
    except (OSError, subprocess.CalledProcessError) as exc:
      if log_fn:
        log_fn(
            "Skipping removal: failed to seal tar into gzip for %s (%s)"
            % (tar_path, exc),
            flush=True,
        )
      return False, None

  if not verify_tar_archive_readable(gz_path):
    if log_fn:
      log_fn(
          "Skipping removal: tar.gz failed integrity check: %s" % gz_path,
          flush=True,
      )
    return False, None
  members_gz = get_file_member_sizes_from_gzip_archive(gz_path)

  if members_tar is not None:
    if members_tar != members_gz:
      if log_fn:
        log_fn(
            "Skipping removal: tar vs tar.gz member mismatch for %s "
            "(uncompressed count=%s gzip count=%s)"
            % (gz_path, len(members_tar), len(members_gz)),
            flush=True,
        )
      return False, None
    return True, members_tar

  return True, members_gz


def replace_corrupt_tar_from_gzip_backup(tar_path, gz_path, pigz_threads):
  """Remove corrupt ``tar_path``, then restore from ``gz_path`` if it exists.

  Returns True if the filesystem is in a consistent state for the caller to
  append: either ``tar_path`` exists (restored from gzip) or both gzip and tar
  are absent (cleared corrupt tar with no backup — next ``tar -r`` creates a
  new archive). Returns False only if gzip restore was attempted but
  ``tar_path`` is still missing afterward.
  """
  try:
    with file_write_lock(tar_path):
      if os.path.isfile(tar_path):
        os.remove(tar_path)
  except OSError:
    return False
  if not os.path.isfile(gz_path):
    return True
  try:
    with file_write_lock(gz_path):
      pigz_decompress_verbose(gz_path, pigz_threads)
    return os.path.isfile(tar_path)
  except (OSError, subprocess.CalledProcessError):
    return os.path.isfile(tar_path)


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
      with tarfile.open(tar_path, "r") as tf:
        by_name = defaultdict(list)
        for m in tf.getmembers():
          if m.isfile():
            by_name[m.name].append(m.size)
        return {name: max(sizes) for name, sizes in by_name.items()}
  except Exception:
    return {}
  finally:
    _remove_read_lock_sidecar(tar_path)


def get_existing_archive_members_for_daily_archive(archive_gz_path):
  """File member sizes for a daily bundle given ``YYYY-MM-DD.tar.gz``.

  Prefers sibling ``.tar`` when present (mutable / post-append). Otherwise
  reads members directly from ``.tar.gz`` (e.g. after seal with no .tar left).
  """
  if not archive_gz_path.endswith(".tar.gz"):
    return {}
  tar_path = archive_gz_path[:-len(".gz")]
  if os.path.isfile(tar_path):
    return get_existing_archive_members(tar_path)
  if not os.path.isfile(archive_gz_path):
    return {}
  try:
    with file_read_lock_wait(archive_gz_path):
      with tarfile.open(archive_gz_path, "r") as tf:
        by_name = defaultdict(list)
        for m in tf.getmembers():
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
):
  """Remove raw stats files only after tar + tar.gz validation and matching member maps.

  For each ``YYYY-MM-DD.tar.gz``, requires: sibling ``.tar`` (if present) passes
  ``verify_tar_archive_readable`` and yields file member sizes; ``.tar.gz`` passes
  the same and yields sizes from the gzip archive only; the two maps must match.
  Then deletes raw paths that match a member name and size.

  Scans all closed segments under ``archive_data_dir`` (same rules as ingest).
  Run after ``seal_dirty_daily_archives``. If only ``.tar.gz`` exists (no sibling
  ``.tar``), gzip alone is validated and its map is used.

  If raw files map to a day but neither ``.tar`` nor ``.tar.gz`` exists yet
  (e.g. ingest already finished and archival was never run for those paths),
  this routine bootstraps the daily archive via ``archive_stats_files`` before
  validation and removal.
  """
  paths = collect_stats_files_in_range(
      archive_data_dir, "all", None, host_name_ext)
  if not paths:
    return
  mapping = build_archive_mapping(paths, tgz_archive_dir)
  if archive_stats_files_fn is None:
    import hpcperfstats.dbload.sync_timedb as _sync_timedb_mod

    archive_stats_files_fn = _sync_timedb_mod.archive_stats_files
  for gz_path, stats_paths in mapping.items():
    if gz_path.endswith(".tar.gz"):
      tar_path = gz_path[:-3]
      if (not os.path.isfile(gz_path) and not os.path.isfile(tar_path)
          and stats_paths):
        if log_fn:
          log_fn(
              "Bootstrapping missing daily archive from %d raw stats file(s): %s"
              % (len(stats_paths), gz_path),
              flush=True,
          )
        if not archive_stats_files_fn((gz_path, list(stats_paths))):
          if log_fn:
            log_fn(
                "Skipping removal: could not bootstrap daily archive: %s"
                % gz_path,
                flush=True,
            )
          continue
    ok, members = validate_sealed_daily_archive_for_raw_removal(
        gz_path, log_fn=log_fn)
    if not ok or members is None:
      continue
    for stats_path in stats_paths:
      for path in get_verified_files_to_remove([stats_path], members):
        if log_fn:
          log_fn(
              "removing stats file (scheduled pigz/removal): " + path,
              flush=True,
          )
        try:
          with file_write_lock(path):
            os.remove(path)
        except OSError as exc:
          if log_fn:
            log_fn("Could not remove %s: %s" % (path, exc), flush=True)


def remove_verified_uncompressed_daily_tars(
    daily_archive_dir,
    *,
    log_fn=log_print,
):
  """Remove ``YYYY-MM-DD.tar`` only after tar/tar.gz verification succeeds.

  This is intended for final/exit maintenance to reclaim disk space while
  keeping periodic maintenance behavior configurable.
  """
  if not daily_archive_dir or not os.path.isdir(daily_archive_dir):
    return
  for tar_path in sorted(iter_daily_tar_paths(daily_archive_dir)):
    gz_path = "%s.gz" % tar_path
    if not os.path.isfile(tar_path):
      continue
    ok, members = validate_sealed_daily_archive_for_raw_removal(
        gz_path, log_fn=log_fn)
    if not ok or members is None:
      continue
    try:
      with file_write_lock(tar_path):
        if os.path.isfile(tar_path):
          os.remove(tar_path)
      if log_fn:
        log_fn(
            "Final/exit maintenance removed verified uncompressed tar: %s" % tar_path,
            flush=True,
        )
    except OSError as exc:
      if log_fn:
        log_fn("Could not remove verified tar %s: %s" % (tar_path, exc), flush=True)


def tar_has_duplicate_file_members(tar_path):
  """Return True if any file member path appears more than once in archive order."""
  if not os.path.isfile(tar_path):
    return False
  try:
    with file_read_lock_wait(tar_path):
      with tarfile.open(tar_path, "r") as tf:
        seen = set()
        for m in tf.getmembers():
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
      with tarfile.open(tar_path, "r") as tin:
        members = tin.getmembers()
        keep = _dedupe_member_indices_keep_largest_file_per_name(members)
        with tarfile.open(tmp_path, "w") as tout:
          for i, m in enumerate(members):
            if i not in keep:
              continue
            if m.isfile():
              fobj = tin.extractfile(m)
              if fobj is None:
                continue
              tout.addfile(m, fobj)
              fobj.close()
            else:
              tout.addfile(m)
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
  """Prefer mutable uncompressed ``.tar`` when both it and ``.tar.gz`` exist.

  Callers may pass either ``YYYY-MM-DD.tar`` or ``YYYY-MM-DD.tar.gz``.
  """
  if path.endswith(".tar.gz"):
    tar_alt = path[:-len(".gz")]
    if os.path.isfile(tar_alt):
      return tar_alt
  return path


def _gzip_backup_and_uncompressed_targets(open_path):
  """Return (gz_path, uncompressed_tar_path) for restore from gzip."""
  if open_path.endswith(".tar.gz"):
    return (open_path, open_path[:-len(".gz")])
  return ("%s.gz" % open_path, open_path)


def get_tar_file_tasks(tar_path):
  """Return list of (tar_path, member_name) for file members only (no dirs).

  If the tar is unreadable and a sibling ``.tar.gz`` exists, delete the
  unreadable tar, restore it with pigz, and retry once.

  When both ``.tar`` and ``.tar.gz`` exist (deferred compression), the
  uncompressed ``.tar`` is used so readers see the latest appends.
  """
  open_path = resolve_preferred_archive_path_for_read(tar_path)

  def _read_members():
    members = []
    with file_read_lock_wait(open_path):
      with tarfile.open(open_path, "r") as archive_tar:
        for member_info in archive_tar.getmembers():
          if not member_info.isfile():
            continue
          members.append((open_path, member_info.name))
    return members

  def _restore_from_gzip():
    gz_path, tar_out = _gzip_backup_and_uncompressed_targets(open_path)
    if not os.path.exists(gz_path):
      return False
    try:
      if os.path.exists(tar_out):
        os.remove(tar_out)
      pigz_decompress_verbose(gz_path, pigz_thread_count)
      return True
    except (OSError, subprocess.CalledProcessError):
      return False

  try:
    return _read_members()
  except (tarfile.TarError, OSError, EOFError):
    log_print(
        "Unable to read archive %s (possible corruption); attempting restore from gzip"
        % open_path
    )
    if not _restore_from_gzip():
      log_print(
          "Archive recovery failed for %s; no usable gzip backup or pigz failed"
          % open_path
      )
      raise
    log_print("Archive recovery succeeded for %s; retrying read" % open_path)
    open_path = resolve_preferred_archive_path_for_read(tar_path)
  return _read_members()


def parse_archive_date_from_daily_tar_path(tar_path):
  """Return date from ``YYYY-MM-DD.tar`` basename, or None if not matched."""
  base = os.path.basename(tar_path)
  if not _DAILY_TAR_BASENAME_RE.match(base):
    return None
  try:
    return datetime.strptime(base[:10], "%Y-%m-%d").date()
  except ValueError:
    return None


def is_daily_tar_gz_dirty(tar_path, gz_path):
  """True if ``.tar`` should be re-sealed into ``.tar.gz`` (gz missing or stale)."""
  if not os.path.isfile(tar_path):
    return False
  if not os.path.exists(gz_path):
    return True
  try:
    return os.path.getmtime(tar_path) > os.path.getmtime(gz_path)
  except OSError:
    return True


def should_seal_daily_tar(
    tar_path,
    gz_path,
    idle_seconds,
    today_local_date,
    seal_immediately_if_dirty=False,
):
  """Whether to seal now: dirty tar/gz pair and idle / prior-day rules.

  If ``seal_immediately_if_dirty`` (e.g. end of a sync_timedb ingest pass), any
  dirty pair is sealed regardless of idle. Otherwise today's archive waits until
  ``idle_seconds`` after its last mtime (prior calendar days seal when dirty).
  """
  if not is_daily_tar_gz_dirty(tar_path, gz_path):
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


def atomic_seal_tar_to_gz(
    tar_path,
    gz_path,
    num_threads,
    compress_level,
    keep_uncompressed_tar,
    log_fn=log_print,
):
  """Compress ``tar_path`` to ``gz_path`` using a temp file, ``pigz -t``, and ``os.replace``.

  The previous ``gz_path`` (if any) stays valid until replace succeeds.
  """
  if not os.path.isfile(tar_path):
    return
  tmp_gz = "%s.tmp" % gz_path
  try:
    if os.path.exists(tmp_gz):
      os.remove(tmp_gz)
  except OSError:
    pass
  try:
    with file_write_lock(tar_path):
      with open(tmp_gz, "wb") as out_f:
        result = subprocess.run(
            [
                pigz_executable(),
                "-c",
                "-%d" % int(compress_level),
                "-p",
                str(num_threads),
                tar_path,
            ],
            stdout=out_f,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
      if result.stderr and log_fn:
        log_fn(result.stderr, flush=True)
      if result.returncode != 0:
        try:
          os.remove(tmp_gz)
        except OSError:
          pass
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            stderr=result.stderr,
        )
      test = subprocess.run(
          [pigz_executable(), "-t", tmp_gz],
          capture_output=True,
          text=True,
          check=False,
      )
      if test.returncode != 0:
        try:
          os.remove(tmp_gz)
        except OSError:
          pass
        raise subprocess.CalledProcessError(
            test.returncode, test.args, stderr=test.stderr)
      with file_write_lock(gz_path):
        os.replace(tmp_gz, gz_path)
  except BaseException:
    try:
      if os.path.exists(tmp_gz):
        os.remove(tmp_gz)
    except OSError:
      pass
    raise
  if log_fn:
    log_fn("Sealed archive %s -> %s" % (tar_path, gz_path), flush=True)
  if keep_uncompressed_tar:
    if log_fn:
      log_fn("Sealed archive retaining uncompressed tar: %s" % tar_path, flush=True)
  else:
    try:
      with file_write_lock(tar_path):
        os.remove(tar_path)
      if log_fn:
        log_fn("Sealed archive removed uncompressed tar: %s" % tar_path, flush=True)
    except OSError:
      pass


def seal_dirty_daily_archives(
    daily_archive_dir,
    *,
    local_tz,
    pigz_threads,
    compress_level,
    keep_uncompressed_tar,
    idle_seconds,
    seal_immediately_if_dirty=False,
    log_fn=log_print,
):
  """Seal every dirty ``YYYY-MM-DD.tar`` under ``daily_archive_dir`` per policy."""
  if not daily_archive_dir or not os.path.isdir(daily_archive_dir):
    return
  today_local = datetime.now(local_tz).date()
  for tar_path in sorted(iter_daily_tar_paths(daily_archive_dir)):
    gz_path = "%s.gz" % tar_path
    if not should_seal_daily_tar(
        tar_path,
        gz_path,
        idle_seconds,
        today_local,
        seal_immediately_if_dirty=seal_immediately_if_dirty,
    ):
      continue
    try:
      atomic_seal_tar_to_gz(
          tar_path,
          gz_path,
          pigz_threads,
          compress_level,
          keep_uncompressed_tar,
          log_fn=log_fn,
      )
    except subprocess.CalledProcessError as exc:
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
  """Group stats file paths by daily archive path (YYYY-MM-DD.tar.gz).

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
      try:
        with file_read_lock_wait(stats_fname):
          with open(stats_fname, "r") as f:
            head = []
            for line in f:
              head.append(line)
              if head and head[-1] and head[-1][0].isdigit():
                break
      except OSError:
        continue
      finally:
        _remove_read_lock_sidecar(stats_fname)
      t, _jid, _host = parse_first_ts_fn(head)
    if t is None:
      log_print(
          "Unable to find first timestamp in %s, skipping archiving"
          % stats_fname
      )
      skipped_no_ts += 1
      continue
    file_date = datetime.fromtimestamp(float(t))
    archive_fname = os.path.join(
        tgz_archive_dir, file_date.strftime("%Y-%m-%d.tar.gz")
    )
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
    try:
      with file_read_lock_wait(stats_fname):
        with open(stats_fname, "r") as f:
          head = []
          for line in f:
            head.append(line)
            if head and head[-1] and head[-1][0].isdigit():
              break
    except OSError:
      continue
    finally:
      _remove_read_lock_sidecar(stats_fname)
    t, _jid, _host = parse_first_ts_fn(head)
    if t is not None:
      timestamps[stats_fname] = t
  return timestamps
