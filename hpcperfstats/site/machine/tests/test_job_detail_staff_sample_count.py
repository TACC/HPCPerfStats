"""Staff-only job_detail field: staff_metrics_distinct_time_count (no live DB)."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from django.test import RequestFactory

from hpcperfstats.site.machine import cache_utils as cu


def _patch_job_detail_for_staff_count(api_module, jid, metrics_distinct_time_count):
  mock_j = MagicMock()
  mock_j.acct_host_list = ["n1.example.com"]
  mock_j.schema = {}
  mock_j.get_llite_delta_by_event.return_value = MagicMock(empty=True)
  t0 = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
  mock_j.start_time = t0
  mock_j.end_time = t0

  job_mock = MagicMock()
  job_mock.jid = jid
  job_mock.username = "u1"
  job_mock.start_time = t0
  job_mock.end_time = t0
  job_mock.metrics_distinct_time_count = metrics_distinct_time_count

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

  vis_qs = MagicMock()
  vis_qs.exists.return_value = True

  return (
      patch.object(api_module, "_require_auth", return_value=None),
      patch.object(api_module, "_apply_non_staff_job_visibility", return_value=vis_qs),
      patch.object(api_module, "get_site_content_cache_timeout", return_value=3600),
      patch.object(api_module.jid_table, "jid_table", return_value=mock_j),
      patch.object(api_module, "build_job_metrics_display_list", return_value=[]),
      patch.object(api_module.cfg, "get_xalt_user", return_value=""),
      patch.object(api_module.cfg, "get_host_name_ext", return_value=""),
      patch.object(api_module, "cached_orm", side_effect=cached_se),
      patch.object(
          api_module,
          "JobListSerializer",
          return_value=MagicMock(data={"jid": jid, "username": "u1"}),
      ),
      patch.object(api_module, "local_timezone", timezone.utc),
  )


def test_job_detail_includes_staff_metrics_distinct_time_count_for_staff():
  from hpcperfstats.site.machine import api

  jid = "test-staff-sample-count-1"
  factory = RequestFactory()
  request = factory.get(f"/api/jobs/{jid}/")
  request.session = {"username": "u1", "is_staff": True}

  ctx = _patch_job_detail_for_staff_count(api, jid, 12_345)

  with ThreadPoolExecutor(max_workers=4) as executor:
    with ExitStack() as stack:
      stack.enter_context(patch.object(api, "_get_small_executor", return_value=executor))
      for cm in ctx:
        stack.enter_context(cm)
      response = api.job_detail(request, jid)

  assert response.status_code == 200
  assert response.data["staff_metrics_distinct_time_count"] == 12_345


def test_job_detail_omits_staff_metrics_distinct_time_count_for_non_staff():
  from hpcperfstats.site.machine import api

  jid = "test-staff-sample-count-2"
  factory = RequestFactory()
  request = factory.get(f"/api/jobs/{jid}/")
  request.session = {"username": "u1", "is_staff": False}

  ctx = _patch_job_detail_for_staff_count(api, jid, 99)

  with ThreadPoolExecutor(max_workers=4) as executor:
    with ExitStack() as stack:
      stack.enter_context(patch.object(api, "_get_small_executor", return_value=executor))
      for cm in ctx:
        stack.enter_context(cm)
      response = api.job_detail(request, jid)

  assert response.status_code == 200
  assert "staff_metrics_distinct_time_count" not in response.data
