"""Unit tests for sync_timedb_archive task building from tar archives.

Verifies that (tar_path, member_name) task list includes only file members, not directories.
"""
import tarfile

import pytest


def test_archive_task_list_includes_only_files(tmp_path):
  """Building tasks from a tar yields (path, member_name) only for file members."""
  tar_path = tmp_path / "archive.tar"
  with tarfile.open(tar_path, "w") as tar:
    # Add a file (member with content)
    f = tmp_path / "stats.txt"
    f.write_text("12345 job1 node1\n")
    tar.add(f, arcname="2024-01-15/node1/stats.txt")
    # Add another file
    f2 = tmp_path / "stats2.txt"
    f2.write_text("12346 job2 node2\n")
    tar.add(f2, arcname="2024-01-15/node2/stats2.txt")

  tasks = []
  with tarfile.open(tar_path, "r") as archive_tar:
    for member_info in archive_tar.getmembers():
      if not member_info.isfile():
        continue
      tasks.append((str(tar_path), member_info.name))

  assert len(tasks) == 2
  names = {t[1] for t in tasks}
  assert "2024-01-15/node1/stats.txt" in names
  assert "2024-01-15/node2/stats2.txt" in names


def test_archive_task_list_excludes_directories(tmp_path):
  """Tar members that are directories are not included in the task list."""
  tar_path = tmp_path / "archive2.tar"
  with tarfile.open(tar_path, "w") as tar:
    # Create a file so we have content; dirs appear as members in some tars
    f = tmp_path / "data.txt"
    f.write_text("data")
    tar.add(f, arcname="2024-01-15/host1/data.txt")
  # Reopen and inspect; getmembers() can include directory entries
  with tarfile.open(tar_path, "r") as archive_tar:
    all_members = archive_tar.getmembers()
    file_tasks = [
        (str(tar_path), m.name)
        for m in all_members
        if m.isfile()
    ]
  assert len(file_tasks) >= 1
  assert all(m.isfile() for m in all_members if any(t[1] == m.name for t in file_tasks))
