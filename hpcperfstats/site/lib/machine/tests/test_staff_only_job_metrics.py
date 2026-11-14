"""Staff-only job metrics (cpu-precision Phase 1b, Grace PMU sharing).

``avg_pmu_running_pct`` / ``avg_pmu_outside_pct`` are counter-coverage
diagnostics: on Horizon GB200 nodes a GPU power service holds most of the
counter time on every job, so researchers would misread them (user decision
2026-11-14: staff only). The pipeline still computes and persists them; every
user-facing metrics surface hides them from non-staff sessions: the Job Detail
metrics table, the Extended Search metric options, and job-list metric
filters (which also feed the job-list histograms).
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from hpcperfstats.analysis.metrics.lib.metrics import STAFF_ONLY_JOB_METRICS
from hpcperfstats.site.lib.machine import cache_utils as cu

pytestmark = pytest.mark.django_db(databases=[])

_STAFF_ONLY = sorted(STAFF_ONLY_JOB_METRICS)


def _metric_rows():
  rows = [{"type": "host_cpu_hw", "metric": "avg_flops", "units": "GF"}]
  rows += [
    {"type": "host_cpu_hw", "metric": m, "units": "%"} for m in _STAFF_ONLY
  ]
  for row in rows:
    row.update(value=1.0, no_data_reason=None)
  return rows


def _job_detail_metrics(is_staff):
  from hpcperfstats.site.lib.machine import api

  jid = "staff-only-metrics-jid"
  request = RequestFactory().get(f"/api/jobs/{jid}/")
  request.session = {"username": "u1", "is_staff": is_staff}
  t0 = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
  job_mock = MagicMock()
  job_mock.jid = jid
  job_mock.username = "u1"
  job_mock.start_time = t0
  job_mock.end_time = t0
  job_mock.metrics_data_set.all.return_value = []

  def cached_se(key, timeout, fn):
    if key.startswith(f"{cu.KEY_JOB}:"):
      return job_mock
    if key.startswith(f"{cu.KEY_PROC_LIST}:"):
      return []
    return fn()

  vis = MagicMock()
  vis.exists.return_value = True
  ctx = (
    patch.object(api, "_require_auth", return_value=None),
    patch.object(api, "job_is_shared_node", return_value=False),
    patch.object(api, "_apply_non_staff_job_visibility", return_value=vis),
    patch.object(api, "get_site_content_cache_timeout", return_value=3600),
    patch.object(
      api, "build_job_metrics_display_list", return_value=_metric_rows()
    ),
    patch.object(api.cfg, "get_xalt_user", return_value=""),
    patch.object(api.cfg, "get_host_name_ext", return_value=""),
    patch.object(api, "cached_orm", side_effect=cached_se),
    patch.object(
      api,
      "load_job_detail_artifact",
      return_value={
        "host_list": ["n1.example.com"],
        "schema": {},
        "fsio": {},
        "gpu_active": None,
        "gpu_utilization_max": None,
        "gpu_utilization_mean": None,
        "gpu_count": None,
      },
    ),
    patch.object(api, "_job_for_detail_list_serializer", return_value=job_mock),
    patch.object(
      api,
      "JobListSerializer",
      return_value=MagicMock(data={"jid": jid, "username": "u1"}),
    ),
    patch.object(api, "local_timezone", UTC),
    patch(
      "hpcperfstats.site.lib.machine.staff_artifact_contract."
      "staff_artifact_contract_payload",
      return_value={},
    ),
  )
  with ThreadPoolExecutor(max_workers=4) as executor, ExitStack() as stack:
    stack.enter_context(
      patch.object(api, "_get_small_executor", return_value=executor)
    )
    for cm in ctx:
      stack.enter_context(cm)
    response = api.job_detail(request, jid)
  assert response.status_code == 200
  return [row["metric"] for row in response.data["metrics_list"]]


def test_job_detail_hides_staff_only_metrics_from_non_staff():
  metrics = _job_detail_metrics(is_staff=False)
  assert "avg_flops" in metrics
  assert not set(_STAFF_ONLY) & set(metrics)


def test_job_detail_shows_staff_only_metrics_to_staff():
  metrics = _job_detail_metrics(is_staff=True)
  assert set(_STAFF_ONLY) <= set(metrics)


def _home_option_metrics(is_staff):
  from hpcperfstats.site.lib.machine import api

  request = RequestFactory().get("/api/home/")
  request.session = {"username": "u1", "is_staff": is_staff}

  class _Future:
    def __init__(self, val):
      self._val = val

    def result(self):
      return self._val

  mock_exec = MagicMock()
  mock_exec.submit.side_effect = lambda _fn, *a, **_k: _Future(a[-1]())
  mock_jd = MagicMock()
  mock_jd.objects.dates.return_value = []
  mock_jd.objects.distinct.return_value.values_list.return_value = []
  (
    mock_jd.objects.exclude.return_value.distinct.return_value.values_list.return_value
  ) = []
  with (
    patch.object(api, "_require_auth", return_value=None),
    patch.object(api, "_get_small_executor", return_value=mock_exec),
    patch.object(api, "cached_orm", side_effect=lambda _k, _t, fn: fn()),
    patch.object(api, "job_data", mock_jd),
    patch.object(
      api, "job_metrics_catalog_entries", return_value=_metric_rows()
    ),
    patch.object(api.cfg, "get_host_name_ext", return_value="hpc"),
  ):
    response = api.home_options(request)
  assert response.status_code == 200
  return [row["metric"] for row in response.data["metrics"]]


def test_home_options_hides_staff_only_metrics_from_non_staff():
  metrics = _home_option_metrics(is_staff=False)
  assert "avg_flops" in metrics
  assert not set(_STAFF_ONLY) & set(metrics)


def test_home_options_shows_staff_only_metrics_to_staff():
  assert set(_STAFF_ONLY) <= set(_home_option_metrics(is_staff=True))


def _job_list_metric_filters(session):
  from hpcperfstats.site.lib.machine import api

  params = {"metrics_avg_flops__gte": "1"}
  params.update({f"metrics_{m}__gte": "50" for m in _STAFF_ONLY})
  request = RequestFactory().get("/api/jobs/", params)
  if session is not None:
    request.session = session
  chain = MagicMock()
  chain.filter.return_value = chain
  chain.order_by.return_value = chain
  with (
    patch.object(api.job_data.objects, "filter", return_value=chain),
    patch.object(
      api, "_apply_non_staff_job_visibility", side_effect=lambda qs, _r: qs
    ),
    patch.object(
      api, "normalize_job_list_query_params", side_effect=lambda f: f
    ),
    patch.object(api, "expand_month_date_to_range", side_effect=lambda f: f),
    patch.object(api, "get_job_list_order_by", return_value="-end_time"),
  ):
    _qs, _fields, cur_metrics, _order = (
      api._build_job_list_queryset_from_request(request)
    )
  return {key.split("__", 1)[0] for key in cur_metrics}


def test_job_list_drops_staff_only_metric_filters_for_non_staff():
  names = _job_list_metric_filters({"username": "u1", "is_staff": False})
  assert names == {"avg_flops"}


def test_job_list_keeps_staff_only_metric_filters_for_staff():
  names = _job_list_metric_filters({"username": "s1", "is_staff": True})
  assert names == {"avg_flops", *_STAFF_ONLY}


def test_job_list_without_session_keeps_metric_filters():
  """Call sites without a session keep today's behavior (as job visibility)."""
  assert _job_list_metric_filters(None) == {"avg_flops", *_STAFF_ONLY}
