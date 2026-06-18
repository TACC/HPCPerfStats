"""job_plots reads job_plot_artifact (L2) before instantiating jid_table."""
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIRequestFactory

from hpcperfstats.site.lib.machine import cache_utils as cu
from hpcperfstats.site.lib.machine.throttles import ExpensiveReadThrottle

pytestmark = pytest.mark.django_db(databases=[])


def test_job_plots_uses_l2_without_jid_table():
  from hpcperfstats.site.lib.machine import api

  factory = APIRequestFactory()
  request = factory.get("/api/jobs/j1/plots/", {"plot": "summary_plot"})
  request.session = {"username": "u1", "is_staff": False}

  job = MagicMock()
  job.jid = "j1"

  plot_item = {"type": "model", "id": "plot123"}

  def cached_se(key, timeout, fn):
    if key == cu.make_job_detail_cache_key("j1"):
      return job
    return fn()

  vis = MagicMock()
  vis.exists.return_value = True
  cache_set = MagicMock()

  with patch.object(ExpensiveReadThrottle, "allow_request", return_value=True), patch.object(
      api, "_require_auth", return_value=None
  ), patch.object(api, "_apply_non_staff_job_visibility", return_value=vis), patch.object(
      api, "get_site_content_cache_timeout", return_value=3600
  ), patch.object(api, "cached_orm", side_effect=cached_se), patch.object(
      api.cache, "get", return_value=None
  ), patch.object(api.cache, "set", cache_set), patch.object(
      api,
      "get_live_distinct_time_count_for_jid",
      return_value=5,
  ), patch.object(
      api,
      "compute_plot_input_fingerprint",
      return_value="testfp",
  ), patch.object(
      api,
      "load_cached_job_plot_entry",
      return_value={"plot_item": plot_item, "unavailable_reason": None},
  ), patch.object(api.jid_table, "jid_table") as mock_jt_cls:
    response = api.job_plots(request, "j1")

  assert response.status_code == 200
  assert response.data["plot_item"] == plot_item
  mock_jt_cls.assert_not_called()
  set_keys = [call.args[0] for call in cache_set.call_args_list]
  assert any(key.startswith("JOB_PLOTS_DATA:j1:summary_plot:testfp") for key in set_keys)


def test_job_plots_zoom_reads_fingerprinted_data_cache_key():
  from hpcperfstats.site.lib.machine import api

  factory = APIRequestFactory()
  request = factory.get("/api/jobs/j1/plots/", {"plot": "summary_plot", "zoom": "1"})
  request.session = {"username": "u1", "is_staff": False}

  job = MagicMock()
  job.jid = "j1"
  vis = MagicMock()
  vis.exists.return_value = True
  observed_get_keys = []

  def cached_se(key, timeout, fn):
    if key == cu.make_job_detail_cache_key("j1"):
      return job
    return fn()

  def cache_get_side_effect(key, default=None):
    observed_get_keys.append(key)
    return default

  with patch.object(ExpensiveReadThrottle, "allow_request", return_value=True), patch.object(
      api, "_require_auth", return_value=None
  ), patch.object(api, "_apply_non_staff_job_visibility", return_value=vis), patch.object(
      api, "get_site_content_cache_timeout", return_value=3600
  ), patch.object(api, "cached_orm", side_effect=cached_se), patch.object(
      api.cache, "get", side_effect=cache_get_side_effect
  ), patch.object(api.cache, "set", return_value=None), patch.object(
      api,
      "get_live_distinct_time_count_for_jid",
      return_value=5,
  ), patch.object(
      api,
      "compute_plot_input_fingerprint",
      return_value="newfp",
  ), patch.object(
      api,
      "load_cached_job_plot_entry",
      return_value=None,
  ):
    response = api.job_plots(request, "j1")

  assert response.status_code in (200, 202)
  assert any(key.startswith("JOB_PLOTS_DATA:j1:summary_plot:newfp") for key in observed_get_keys)
