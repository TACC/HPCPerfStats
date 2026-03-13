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


def collect_stats_files_in_range(directory, startdate, enddate):
  """Scan directory for stats files (under c* or v* subdirs) whose mtime is in [startdate, enddate). Returns sorted list of file paths. startdate may be 'all' (then enddate is exclusive upper bound)."""
  stats_files = []
  for entry in os.scandir(directory):
    if entry.is_file() or not (
        entry.name.startswith("c") or entry.name.startswith("v")):
      continue
    for stats_file in os.scandir(entry.path):
      if not stats_file.is_file() or stats_file.name.startswith("."):
        continue
      if stats_file.name.startswith("current"):
        continue
      try:
        mtime_fdate = datetime.fromtimestamp(
            int(os.path.getmtime(stats_file.path))
        )
        fdate = mtime_fdate
      except Exception as e:
        log_print("error in obtaining timestamp of raw data files: ", str(e))
        continue
      if startdate == "all":
        if fdate > enddate:
          continue
        stats_files.append(stats_file.path)
        continue
      if fdate <= startdate - timedelta(days=1) or fdate > enddate:
        continue
      stats_files.append(stats_file.path)
  stats_files.sort(key=os.path.basename)
  return stats_files


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
