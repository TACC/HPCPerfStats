"""Tests for update_xalt.run_update_xalt_for_range (ORM mocked)."""

from datetime import date
from unittest.mock import MagicMock, patch

from hpcperfstats.site.lib.machine import update_xalt


def test_run_update_xalt_for_range_skips_exec_path_with_usr_segment():
  lines = []
  day = date(2024, 1, 1)
  run_skip = MagicMock(exec_path="/usr/bin/foo")
  run_keep = MagicMock(exec_path="/opt/app/bin/job")

  def fake_daterange(start, end, inclusive_end=True):
    return [day]

  def filter_runs(**kwargs):
    jid = kwargs["job_id"]
    if jid == 101:
      return [run_skip]
    return [run_keep]

  using_ret = MagicMock()
  using_ret.filter.side_effect = filter_runs
  run_mock = MagicMock()
  run_mock.objects.using.return_value = using_ret

  jd_tail = MagicMock()
  jd_tail.values_list.return_value = [101, 102]
  jd_mock = MagicMock()
  jd_mock.objects.filter.return_value = jd_tail

  with patch.object(update_xalt, "daterange", fake_daterange), patch.object(
      update_xalt, "job_data", jd_mock
  ), patch.object(update_xalt, "run", run_mock):
    update_xalt.run_update_xalt_for_range(day, day, log_fn=lines.append)

  assert "2024-01-01" in lines
  assert any("jid=102" in ln and "/opt/app/bin/job" in ln for ln in lines)
  assert not any(ln.startswith("  jid=101") for ln in lines)
