"""Pure helpers for sync_timedb archiving, tar utilities, and file discovery (no Django). Used by sync_timedb and by unit tests."""
import os
import subprocess
import tarfile
from datetime import datetime, timedelta

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload.pigz_cli import pigz_decompress_verbose
from hpcperfstats.dbload.sync_timedb_parsing import parse_first_timestamp_line
from hpcperfstats.file_locking import LOCK_SUFFIX, file_read_lock_wait
from hpcperfstats.print_utils import log_print

pigz_thread_count = max(1, cfg.get_worker_thread_count(4))


def _is_lock_file_name(name):
  """Return True for sidecar/advisory lock files (e.g. *.fnctl.lock)."""
  # We intentionally skip generic *.lock too, because different lock
  # implementations may exist on the filesystem (and we don't want them
  # mistaken for stats data files during archive discovery).
  return name.endswith(LOCK_SUFFIX) or name.endswith(".lock")


def get_tar_member_name(file_path):
  """Return the name used for a file inside a tar (path without leading slash)."""
  return file_path.lstrip("/")


def get_existing_archive_members(tar_path):
  """Read tar at tar_path and return dict of member name -> size. Returns {} on error or missing file."""
  if not os.path.exists(tar_path):
    return {}
  try:
    with file_read_lock_wait(tar_path):
      with tarfile.open(tar_path, "r") as tf:
        return {m.name: m.size for m in tf.getmembers()}
  except Exception:
    return {}


def get_tar_file_tasks(tar_path):
  """Return list of (tar_path, member_name) for file members only (no dirs).

  If the tar is unreadable and a sibling ``.tar.gz`` exists, delete the
  unreadable tar, restore it with pigz, and retry once.
  """
  def _read_members():
    members = []
    with file_read_lock_wait(tar_path):
      with tarfile.open(tar_path, 'r') as archive_tar:
        for member_info in archive_tar.getmembers():
          if not member_info.isfile():
            continue
          members.append((tar_path, member_info.name))
    return members

  def _restore_from_gzip():
    gz_path = "%s.gz" % tar_path
    if not os.path.exists(gz_path):
      return False
    try:
      if os.path.exists(tar_path):
        os.remove(tar_path)
      pigz_decompress_verbose(gz_path, pigz_thread_count)
      return True
    except (OSError, subprocess.CalledProcessError):
      return False

  try:
    return _read_members()
  except (tarfile.TarError, OSError, EOFError):
    log_print(
        "Unable to read tar %s (possible corruption); attempting restore from %s.gz"
        % (tar_path, tar_path)
    )
    if not _restore_from_gzip():
      log_print(
          "Tar recovery failed for %s; no usable gzip backup or pigz failed"
          % tar_path
      )
      raise
    log_print("Tar recovery succeeded for %s; retrying tar read" % tar_path)
  return _read_members()


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


def collect_stats_files_in_range(directory, startdate, enddate, host_name_ext):
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
        fdate_mtime = datetime.fromtimestamp(
            int(os.path.getmtime(stats_file.path))
        )
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
        try:
          sort_epoch = int(os.path.getmtime(stats_file.path))
        except Exception:
          sort_epoch = None

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
    directory, startdate, enddate, host_name_ext, processed_files
):
  """Return newest-first files still pending after excluding processed files."""
  discovered_files = collect_stats_files_in_range(
      directory, startdate, enddate, host_name_ext)
  processed_set = set(processed_files or [])
  return [path for path in discovered_files if path not in processed_set]


def build_archive_mapping(
    files_to_be_archived, tgz_archive_dir, parse_first_ts_fn=None
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
