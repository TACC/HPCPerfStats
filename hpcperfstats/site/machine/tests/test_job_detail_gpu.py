"""Tests for job_detail GPU utilization (DB aggregate path; no DB required)."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from hpcperfstats.analysis.gen.jid_table import JID_TABLE_HOST_QUERY_BATCH
from hpcperfstats.analysis.metrics.gpu_job_detail_summary import gpu_count_total_for_job_window
from hpcperfstats.site.machine import cache_utils as cu
from hpcperfstats.site.machine import job_detail_artifacts as job_detail_artifacts_mod

pytestmark = pytest.mark.django_db(databases=[])
def _patch_job_detail_context(api_module, jid, gpu_agg, gpu_count_cached=None):
  """Return context manager that stubs job_detail dependencies (no ORM)."""
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
  # Empty metrics so job_detail falls back to host_data aggregate cache path.
  job_mock.metrics_data_set.all.return_value = []

  def cached_se(key, timeout, fn):
    if key.startswith(f"{cu.KEY_JOB}:"):
      return job_mock
    if key.startswith(f"{cu.KEY_GPU_AGG}:"):
      return gpu_agg
    if key.startswith(f"{cu.KEY_GPU_COUNT}:"):
      return gpu_count_cached
    if key.startswith(f"{cu.KEY_PROC_LIST}:"):
      return []
    return fn()

  vis = MagicMock()
  vis.exists.return_value = True
  detail_payload = {
      "host_list": mock_j.acct_host_list,
      "schema": {},
      "fsio": {},
      "gpu_active": None,
      "gpu_utilization_max": None,
      "gpu_utilization_mean": None,
      "gpu_count": gpu_count_cached,
  }
  multiprecision_payload = {
      "cpu_plot_item": None,
      "cpu_unavailable_reason": (
          "Missing CPU precision-width mix metrics in job metrics "
          "(need positive vecpercent_* shares)."
      ),
      "gpu_plot_item": None,
      "gpu_unavailable_reason": (
          "Missing GPU precision-width mix metrics in job metrics "
          "(need positive avg_*_active shares)."
      ),
  }
  if gpu_agg and gpu_agg.get("cnt", 0) > 2:
    detail_payload["gpu_active"] = 3 if float(gpu_agg.get("vmax", 0.0) or 0.0) > 0.0 else 0
    detail_payload["gpu_utilization_max"] = float(gpu_agg.get("vmax", 0.0) or 0.0)
    detail_payload["gpu_utilization_mean"] = float(gpu_agg.get("vmean", 0.0) or 0.0)
  return (
      patch.object(api_module, "_require_auth", return_value=None),
      patch.object(
          api_module, "_apply_non_staff_job_visibility", return_value=vis
      ),
      patch.object(api_module, "get_site_content_cache_timeout", return_value=3600),
      patch.object(api_module.jid_table, "jid_table", return_value=mock_j),
      patch.object(api_module, "build_job_metrics_display_list", return_value=[]),
      patch.object(api_module.cfg, "get_xalt_user", return_value=""),
      patch.object(api_module.cfg, "get_host_name_ext", return_value=""),
      patch.object(api_module, "cached_orm", side_effect=cached_se),
      patch.object(api_module, "load_job_detail_artifact", side_effect=[
          detail_payload, multiprecision_payload]),
      patch.object(
          api_module,
          "JobListSerializer",
          return_value=MagicMock(data={"jid": jid, "username": "u1"}),
      ),
      patch.object(api_module, "local_timezone", timezone.utc),
  )


def test_job_detail_gpu_stats_from_aggregate_dict():
  """GPU fields reflect cached ORM aggregate (fast path)."""
  from hpcperfstats.site.machine import api

  jid = "test-gpu-jid-1"
  factory = RequestFactory()
  request = factory.get(f"/api/jobs/{jid}/")
  request.session = {"username": "u1", "is_staff": False}

  gpu_agg = {"cnt": 4, "vmax": 250.0, "vmean": 80.0}
  ctx = _patch_job_detail_context(api, jid, gpu_agg, gpu_count_cached=8)

  with ThreadPoolExecutor(max_workers=4) as executor:
    with ExitStack() as stack:
      stack.enter_context(patch.object(api, "_get_small_executor", return_value=executor))
      for cm in ctx:
        stack.enter_context(cm)
      response = api.job_detail(request, jid)

  assert response.status_code == 200
  data = response.data
  assert data["gpu_utilization_max"] == 250.0
  assert data["gpu_active"] == 3
  assert data["gpu_utilization_mean"] == 80.0
  assert data["gpu_count"] == 8
  assert "multiprecision_cpu_plot_item" in data
  assert "multiprecision_gpu_plot_item" in data


def test_job_detail_gpu_stats_none_when_two_or_fewer_samples():
  """Same threshold as before: need more than two samples."""
  from hpcperfstats.site.machine import api

  jid = "test-gpu-jid-2"
  factory = RequestFactory()
  request = factory.get(f"/api/jobs/{jid}/")
  request.session = {"username": "u1", "is_staff": False}

  gpu_agg = {"cnt": 2, "vmax": 60.0, "vmean": 55.0}
  ctx = _patch_job_detail_context(api, jid, gpu_agg)

  with ThreadPoolExecutor(max_workers=4) as executor:
    with ExitStack() as stack:
      stack.enter_context(patch.object(api, "_get_small_executor", return_value=executor))
      for cm in ctx:
        stack.enter_context(cm)
      response = api.job_detail(request, jid)

  assert response.status_code == 200
  assert response.data["gpu_active"] is None
  assert response.data["gpu_utilization_max"] is None
  assert response.data["gpu_utilization_mean"] is None
  assert response.data["gpu_count"] is None


def test_compute_job_gpu_stats_helper_matches_job_detail_gpu_logic():
  """Shared helper returns active/max/mean/count from cached aggregate values."""
  from hpcperfstats.site.machine import api

  job = MagicMock()
  job.jid = "test-gpu-jid-helper"
  j = MagicMock()
  t0 = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
  j.start_time = t0
  j.end_time = t0
  j.acct_host_list = ["n1.example.com"]

  def cached_se(key, timeout, fn):
    del timeout, fn
    if key.startswith(f"{cu.KEY_GPU_AGG}:"):
      return [
          {"host": "n1.example.com", "dev": "0", "event": "gpu_util", "cnt": 4, "vmax": 250.0, "vmean": 80.0},
      ]
    if key.startswith(f"{cu.KEY_GPU_COUNT}:"):
      return 8
    return None

  with patch.object(api, "cached_orm", side_effect=cached_se):
    gpu_active, gpu_max, gpu_mean, gpu_count = api._compute_job_gpu_stats(job, j, 3600)

  assert gpu_max == 250.0
  assert gpu_active == 1
  assert gpu_mean == 80.0
  assert gpu_count == 8


def test_compute_job_gpu_stats_helper_uses_host_device_aware_active_count():
  """Active GPUs are counted per (host,dev) when v3 aggregate rows are available."""
  from hpcperfstats.site.machine import api

  job = MagicMock()
  job.jid = "test-gpu-jid-host-aware"
  j = MagicMock()
  t0 = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
  j.start_time = t0
  j.end_time = t0
  j.acct_host_list = ["n1.example.com", "n2.example.com"]

  def cached_se(key, timeout, fn):
    del timeout, fn
    if key.startswith(f"{cu.KEY_GPU_AGG}:"):
      return [
        {"host": "n1.example.com", "dev": "0", "event": "gpu_util", "cnt": 4, "vmax": 90.0, "vmean": 50.0},
        {"host": "n1.example.com", "dev": "1", "event": "gpu_util", "cnt": 4, "vmax": 0.0, "vmean": 0.0},
        {"host": "n2.example.com", "dev": "0", "event": "gpu_util", "cnt": 4, "vmax": 70.0, "vmean": 40.0},
      ]
    if key.startswith(f"{cu.KEY_GPU_COUNT}:"):
      return 3
    return None

  with patch.object(api, "cached_orm", side_effect=cached_se):
    gpu_active, gpu_max, gpu_mean, gpu_count = api._compute_job_gpu_stats(job, j, 3600)

  assert gpu_active == 2
  assert gpu_max == 160.0
  assert gpu_mean == 90.0
  assert gpu_count == 3


def test_gpu_agg_rows_for_job_batches_host__in():
  """GPU aggregate ORM path uses jid_table-sized host__in chunks."""
  from hpcperfstats.site.machine import api

  t0 = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
  j = MagicMock()
  j.start_time = t0
  j.end_time = t0
  n = JID_TABLE_HOST_QUERY_BATCH + 2
  j.acct_host_list = ["h{0}.x".format(i) for i in range(n)]
  chunk_sizes = []

  class Qs:
    def values(self, *args):
      return self

    def annotate(self, **kwargs):
      return self

    def __iter__(self):
      return iter(())

  class Mgr:
    def filter(self, **kwargs):
      chunk_sizes.append(len(kwargs.get("host__in") or []))
      return Qs()

  with patch(
      "hpcperfstats.analysis.metrics.gpu_job_detail_summary.host_data.objects",
      Mgr(),
  ):
    api._gpu_agg_rows_for_job(j)
  assert chunk_sizes == [JID_TABLE_HOST_QUERY_BATCH, 2]


def test_job_detail_gpu_from_metrics_data_skips_host_data_cache():
  """When all four detail_gpu_* rows exist, do not call host_data GPU cache path."""
  from hpcperfstats.site.machine import api

  jid = "test-gpu-metrics-jid"
  factory = RequestFactory()
  request = factory.get(f"/api/jobs/{jid}/")
  request.session = {"username": "u1", "is_staff": False}

  class _MRow:
    def __init__(self, metric, value):
      self.metric = metric
      self.value = value

  gpu_rows = [
      _MRow("detail_gpu_active", 2.0),
      _MRow("detail_gpu_util_max", 99.5),
      _MRow("detail_gpu_util_mean", 45.25),
      _MRow("detail_gpu_count", 4.0),
  ]

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
  job_mock.metrics_data_set.all.return_value = gpu_rows

  gpu_cache_calls = []

  def cached_se(key, timeout, fn):
    if key.startswith(f"{cu.KEY_JOB}:"):
      return job_mock
    if key.startswith(f"{cu.KEY_GPU_AGG}:") or key.startswith(
        f"{cu.KEY_GPU_COUNT}:"
    ):
      gpu_cache_calls.append(key)
      return fn()
    if key.startswith(f"{cu.KEY_PROC_LIST}:"):
      return []
    return fn()

  vis = MagicMock()
  vis.exists.return_value = True
  ctx = (
      patch.object(api, "_require_auth", return_value=None),
      patch.object(
          api, "_apply_non_staff_job_visibility", return_value=vis
      ),
      patch.object(api, "get_site_content_cache_timeout", return_value=3600),
      patch.object(api.jid_table, "jid_table", return_value=mock_j),
      patch.object(api, "build_job_metrics_display_list", return_value=[]),
      patch.object(api.cfg, "get_xalt_user", return_value=""),
      patch.object(api.cfg, "get_host_name_ext", return_value=""),
      patch.object(api, "cached_orm", side_effect=cached_se),
      patch.object(
          api,
          "load_job_detail_artifact",
          return_value={
              "host_list": mock_j.acct_host_list,
              "schema": {},
              "fsio": {},
              "gpu_active": 2,
              "gpu_utilization_max": 99.5,
              "gpu_utilization_mean": 45.25,
              "gpu_count": 4,
          },
      ),
      patch.object(
          api,
          "JobListSerializer",
          return_value=MagicMock(data={"jid": jid, "username": "u1"}),
      ),
      patch.object(api, "local_timezone", timezone.utc),
  )

  with ThreadPoolExecutor(max_workers=4) as executor:
    with ExitStack() as stack:
      stack.enter_context(patch.object(api, "_get_small_executor", return_value=executor))
      for cm in ctx:
        stack.enter_context(cm)
      response = api.job_detail(request, jid)

  assert response.status_code == 200
  assert response.data["gpu_active"] == 2
  assert response.data["gpu_utilization_max"] == 99.5
  assert response.data["gpu_utilization_mean"] == 45.25
  assert response.data["gpu_count"] == 4
  assert gpu_cache_calls == []


def test_gpu_count_total_prefers_nvidia_gpu_over_amd_gpu():
  t0 = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
  j = MagicMock()
  j.start_time = t0
  j.end_time = t0
  j.acct_host_list = ["n1.example.com"]

  class _Query:
    def __init__(self, rows):
      self._rows = rows

    def values(self, *args):
      return self

    def annotate(self, **kwargs):
      return self._rows

  class _Mgr:
    def filter(self, **kwargs):
      if kwargs.get("type") == "nvidia_gpu":
        return _Query([{"host": "n1.example.com", "mv": 8.0}])
      if kwargs.get("type") == "amd_gpu":
        return _Query([{"host": "n1.example.com", "mv": 2.0}])
      return _Query([])

  with patch("hpcperfstats.analysis.metrics.gpu_job_detail_summary.host_data.objects", _Mgr()):
    assert gpu_count_total_for_job_window(j) == 8


def test_gpu_count_total_returns_none_when_no_gpu_rows_exist():
  t0 = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
  j = MagicMock()
  j.start_time = t0
  j.end_time = t0
  j.acct_host_list = ["n1.example.com"]

  class _Query:
    def values(self, *args):
      return self

    def annotate(self, **kwargs):
      return []

  class _Mgr:
    def filter(self, **kwargs):
      return _Query()

  with patch("hpcperfstats.analysis.metrics.gpu_job_detail_summary.host_data.objects", _Mgr()):
    assert gpu_count_total_for_job_window(j) is None


def test_compute_job_gpu_stats_degrades_when_cache_set_fails():
  """Real ``cached_orm``: cache miss + set error still returns aggregate + count from DB fns."""
  from hpcperfstats.site.machine import api
  from hpcperfstats.site.machine import cache_utils as cu

  mock_cache = MagicMock()
  mock_cache.get.side_effect = lambda key, default=None: default
  mock_cache.set.side_effect = OSError("redis read-only")

  job = MagicMock()
  job.jid = "j-gpu-cache-set-fail"
  j = MagicMock()
  t0 = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
  j.start_time = t0
  j.end_time = t0
  j.acct_host_list = ["n1.example.com"]

  fake_agg = [
      {
          "host": "n1.example.com",
          "dev": "0",
          "event": "gpu_util",
          "cnt": 4,
          "vmax": 250.0,
          "vmean": 80.0,
      },
  ]

  with patch.object(cu, "cache", mock_cache):
    with patch.object(api, "_gpu_agg_rows_for_job", return_value=fake_agg):
      with patch(
          "hpcperfstats.analysis.metrics.gpu_job_detail_summary.gpu_count_total_for_job_window",
          return_value=6,
      ):
        gpu_active, gpu_max, gpu_mean, gpu_count = api._compute_job_gpu_stats(
            job, j, 3600
        )

  assert gpu_max == 250.0
  assert gpu_mean == 80.0
  assert gpu_active == 1
  assert gpu_count == 6
  assert mock_cache.set.call_count >= 2


def test_compute_job_gpu_stats_degrades_when_cache_get_raises():
  """Real ``cached_orm``: cache.get failure skips set and still runs query fns."""
  from hpcperfstats.site.machine import api
  from hpcperfstats.site.machine import cache_utils as cu

  mock_cache = MagicMock()
  mock_cache.get.side_effect = ConnectionError("redis down")

  job = MagicMock()
  job.jid = "j-gpu-cache-get-fail"
  j = MagicMock()
  t0 = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
  j.start_time = t0
  j.end_time = t0
  j.acct_host_list = ["n1.example.com"]

  fake_agg = [
      {
          "host": "n1.example.com",
          "dev": "0",
          "event": "gpu_util",
          "cnt": 4,
          "vmax": 100.0,
          "vmean": 50.0,
      },
  ]

  with patch.object(cu, "cache", mock_cache):
    with patch.object(api, "_gpu_agg_rows_for_job", return_value=fake_agg):
      with patch(
          "hpcperfstats.analysis.metrics.gpu_job_detail_summary.gpu_count_total_for_job_window",
          return_value=2,
      ):
        gpu_active, gpu_max, gpu_mean, gpu_count = api._compute_job_gpu_stats(
            job, j, 3600
        )

  assert gpu_max == 100.0
  assert gpu_mean == 50.0
  assert gpu_active == 1
  assert gpu_count == 2
  mock_cache.set.assert_not_called()


def test_multiprecision_mix_payload_staff_reasons_align_with_plot_tabs():
  """Unavailable reasons follow the same Missing-/metrics style for CPU and GPU pies."""
  payload = job_detail_artifacts_mod._multiprecision_mix_payload({})
  cpu_r = payload["cpu_unavailable_reason"] or ""
  assert "Missing CPU precision-width mix metrics in job metrics" in cpu_r
  assert "vecpercent_*" in cpu_r
  gpu_r = payload["gpu_unavailable_reason"] or ""
  assert "Missing GPU precision-width mix metrics in job metrics" in gpu_r
  assert "avg_*_active" in gpu_r
  assert payload["cpu_plot_item"] is None
  assert payload["gpu_plot_item"] is None


def test_multiprecision_pie_uses_category10_colors_and_inset_layout():
  """Regression: wedges use d3 Category10 colors and fit inside the frame."""
  import json

  item, reason = job_detail_artifacts_mod._pie_item_from_precision_mix(
      precision_mix={"FP32": 60.0, "FP64": 40.0},
      title="CPU Multiprecision Mix",
      empty_reason="empty",
      help_plot_key="jobDetailPlot_multiprecision_cpu",
      label_order=job_detail_artifacts_mod._CPU_PRECISION_LABEL_ORDER,
  )
  assert reason is None
  assert item is not None
  doc = json.dumps(item)
  assert "#1f77b4" in doc
  assert "#ff7f0e" in doc
  assert str(job_detail_artifacts_mod._MULTIPRECISION_PIE_RADIUS) in doc
  assert "min_border_bottom" in doc


def test_multiprecision_mix_payload_does_not_query_host_data(monkeypatch):
  """Regression: GPU multiprecision pie must come from metrics_data only.

  Asserts ``_multiprecision_mix_payload`` no longer depends on a ``jt`` /
  host_data argument and renders the GPU pie from persisted ``avg_*_active``
  metric values. Backs the host-data-read-boundary policy for prewarm."""
  metric_values = {
      "vecpercent_64b": 30.0,
      "vecpercent_32b": 70.0,
      "avg_tensor_active": 12.0,
      "avg_fp16_active": 24.0,
      "avg_fp32_active": 36.0,
      "avg_fp64_active": 28.0,
  }
  payload = job_detail_artifacts_mod._multiprecision_mix_payload(metric_values)
  assert payload["cpu_unavailable_reason"] is None
  assert payload["gpu_unavailable_reason"] is None
  assert payload["cpu_plot_item"] is not None
  assert payload["gpu_plot_item"] is not None
  assert not hasattr(job_detail_artifacts_mod, "gpu_precision_mix_rows_for_job_window")
  assert not hasattr(job_detail_artifacts_mod, "reduce_gpu_precision_mix")
