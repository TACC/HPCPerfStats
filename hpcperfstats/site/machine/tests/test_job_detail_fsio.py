"""Tests for job_detail file-system section (llite vs NFS fallback)."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
from django.test import RequestFactory

from hpcperfstats.site.machine import cache_utils as cu


def _patch_job_detail_fsio_context(api_module, jid, mock_j):
  """Stub job_detail with a custom jid_table mock (no ORM)."""
  job_mock = MagicMock()
  job_mock.jid = jid
  job_mock.username = "u1"
  t0 = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
  job_mock.start_time = t0
  job_mock.end_time = t0

  def cached_se(key, timeout, fn):
    if key.startswith(f"{cu.KEY_JOB}:"):
      return job_mock
    if key.startswith(f"{cu.KEY_GPU_AGG}:"):
      return None
    if key.startswith(f"{cu.KEY_GPU_COUNT}:"):
      return None
    if key.startswith(f"{cu.KEY_PROC_LIST}:"):
      return []
    return fn()

  vis = MagicMock()
  vis.exists.return_value = True
  return (
      patch.object(api_module, "_require_auth", return_value=None),
      patch.object(
          api_module, "_apply_non_staff_job_visibility", return_value=vis
      ),
      patch.object(api_module, "get_site_content_cache_timeout", return_value=3600),
      patch.object(api_module.jid_table, "jid_table", return_value=mock_j),
      patch.object(api_module, "build_job_metrics_display_list", return_value=[]),
      patch.object(api_module.cfg, "get_xalt_user", return_value=""),
      patch.object(api_module.cfg, "get_host_name_ext", return_value="example.com"),
      patch.object(api_module, "cached_orm", side_effect=cached_se),
      patch.object(
          api_module,
          "JobListSerializer",
          return_value=MagicMock(data={"jid": jid, "username": "u1"}),
      ),
      patch.object(api_module, "local_timezone", timezone.utc),
  )


def test_job_detail_fsio_uses_nfs_when_no_llite():
  """When llite is empty, populate fsio from NFS byte aggregates."""
  from hpcperfstats.site.machine import api

  jid = "test-fsio-nfs-1"
  factory = RequestFactory()
  request = factory.get(f"/api/jobs/{jid}/")
  request.session = {"username": "u1", "is_staff": False}

  mock_j = MagicMock()
  mock_j.acct_host_list = ["n1.example.com"]
  mock_j.schema = {}
  mock_j.get_llite_delta_by_event.return_value = MagicMock(empty=True)
  mock_j.get_nfs_delta_totals_mb.return_value = [12.5, 3.25]
  t0 = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
  mock_j.start_time = t0
  mock_j.end_time = t0

  ctx = _patch_job_detail_fsio_context(api, jid, mock_j)

  with ThreadPoolExecutor(max_workers=4) as executor:
    with ExitStack() as stack:
      stack.enter_context(patch.object(api, "_get_small_executor", return_value=executor))
      for cm in ctx:
        stack.enter_context(cm)
      response = api.job_detail(request, jid)

  assert response.status_code == 200
  assert response.data["fsio"] == {"nfs": [12.5, 3.25]}
  mock_j.get_nfs_delta_totals_mb.assert_called_once()


def test_job_detail_fsio_prefers_llite_over_nfs():
  """When llite is present, do not call NFS."""
  from hpcperfstats.site.machine import api

  jid = "test-fsio-llite-1"
  factory = RequestFactory()
  request = factory.get(f"/api/jobs/{jid}/")
  request.session = {"username": "u1", "is_staff": False}

  mock_j = MagicMock()
  mock_j.acct_host_list = ["n1.example.com"]
  mock_j.schema = {}
  llite_df = pd.DataFrame(
      [
          {"event": "read_bytes", "delta_sum": 1048576.0},
          {"event": "write_bytes", "delta_sum": 2097152.0},
      ]
  )
  mock_j.get_llite_delta_by_event.return_value = llite_df
  t0 = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
  mock_j.start_time = t0
  mock_j.end_time = t0

  ctx = _patch_job_detail_fsio_context(api, jid, mock_j)

  with ThreadPoolExecutor(max_workers=4) as executor:
    with ExitStack() as stack:
      stack.enter_context(patch.object(api, "_get_small_executor", return_value=executor))
      for cm in ctx:
        stack.enter_context(cm)
      response = api.job_detail(request, jid)

  assert response.status_code == 200
  assert response.data["fsio"]["llite"][0] == 1.0
  assert response.data["fsio"]["llite"][1] == 2.0
  mock_j.get_nfs_delta_totals_mb.assert_not_called()
