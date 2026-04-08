"""job_plots reads job_plot_artifact (L2) before instantiating jid_table."""
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIRequestFactory

from hpcperfstats.site.machine import cache_utils as cu

pytestmark = pytest.mark.django_db(databases=[])


def test_job_plots_uses_l2_without_jid_table():
  from hpcperfstats.site.machine import api

  factory = APIRequestFactory()
  request = factory.get("/api/jobs/j1/plots/", {"plot": "summary_plot"})
  request.session = {"username": "u1", "is_staff": False}

  job = MagicMock()
  job.jid = "j1"

  plot_item = {"type": "model", "id": "plot123"}

  def cached_se(key, timeout, fn):
    if key.startswith(f"{cu.KEY_JOB}:j1"):
      return job
    return None

  vis = MagicMock()
  vis.exists.return_value = True

  with patch.object(api, "_require_auth", return_value=None), patch.object(
      api, "_apply_non_staff_job_visibility", return_value=vis
  ), patch.object(api, "get_site_content_cache_timeout", return_value=3600), patch.object(
      api, "cached_orm", side_effect=cached_se
  ), patch.object(api.cache, "get", return_value=None), patch.object(
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
