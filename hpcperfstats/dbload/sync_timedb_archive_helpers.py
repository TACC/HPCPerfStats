"""Pure helpers for sync_timedb archiving, tar utilities, and file discovery (no Django). Used by sync_timedb and by unit tests."""
import os
import tarfile
from datetime import datetime, timedelta

from hpcperfstats.dbload.sync_timedb_parsing import parse_first_timestamp_line
from hpcperfstats.print_utils import log_print


def get_tar_member_name(file_path):
  """Return the name used for a file inside a tar (path without leading slash)."""
  return file_path.lstrip("/")


def get_existing_archive_members(tar_path):
  """Read tar at tar_path and return dict of member name -> size. Returns {} on error or missing file."""
  if not os.path.exists(tar_path):
    return {}
  try:
    with tarfile.open(tar_path, "r") as tf:
      return {m.name: m.size for m in tf.getmembers()}
  except Exception:
    return {}


def get_tar_file_tasks(tar_path):
  """Return list of (tar_path, member_name) for file members only (no dirs)."""
  tasks = []
  with tarfile.open(tar_path, 'r') as archive_tar:
    for member_info in archive_tar.getmembers():
      if not member_info.isfile():
        continue
      tasks.append((tar_path, member_info.name))
  return tasks


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


def collect_stats_files_in_range(directory, startdate, enddate, host_name_ext):
  """Scan ``archive_dir`` for stats files in immediate subdirs whose names end
  with ``host_name_ext`` (same value as ``DEFAULT.host_name_ext`` in ini).

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
      if stats_file.name.startswith("current"):
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


def build_archive_mapping(
    files_to_be_archived, tgz_archive_dir, parse_first_ts_fn=None
):
  """Group stats file paths by daily archive path (YYYY-MM-DD.tar.gz). Uses parse_first_ts_fn to get timestamp from each file; skips today and files with no timestamp. Returns dict archive_fname -> list of stats file paths."""
  if parse_first_ts_fn is None:
    parse_first_ts_fn = parse_first_timestamp_line
  ar_file_mapping = {}
  for stats_fname in files_to_be_archived:
    try:
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
      continue
    file_date = datetime.fromtimestamp(float(t))
    if file_date.date() == datetime.today().date():
      continue
    archive_fname = os.path.join(
        tgz_archive_dir, file_date.strftime("%Y-%m-%d.tar.gz")
    )
    if archive_fname not in ar_file_mapping:
      ar_file_mapping[archive_fname] = []
    ar_file_mapping[archive_fname].append(stats_fname)
  return ar_file_mapping
