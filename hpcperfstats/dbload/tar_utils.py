"""Tar archive helpers for dbload (no Django dependency)."""
import tarfile


def get_tar_file_tasks(tar_path):
  """Return list of (tar_path, member_name) for file members only (no dirs)."""
  tasks = []
  with tarfile.open(tar_path, 'r') as archive_tar:
    for member_info in archive_tar.getmembers():
      if not member_info.isfile():
        continue
      tasks.append((tar_path, member_info.name))
  return tasks
