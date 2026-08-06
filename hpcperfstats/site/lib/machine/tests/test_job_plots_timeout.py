"""job_plots artifact-only loading/ready contracts (no live host_data compute)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

pytestmark = pytest.mark.django_db(databases=[])


@pytest.fixture(autouse=True)
def _patch_plot_fingerprint_dependencies(monkeypatch):
  """Avoid DB access for fingerprint/L2 probes in databases=[] tests."""
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.api.compute_plot_input_fingerprint",
      lambda _job, *_a, **_k: "testfp",
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.api.load_cached_job_plot_entry",
      lambda *_a, **_k: None,
  )


def _patch_allow_job_visibility():
  """job_plots calls _apply_non_staff_job_visibility(...).exists() before other mocks apply."""
  m = MagicMock()
  m.exists.return_value = True
  return patch(
      "hpcperfstats.site.lib.machine.api._apply_non_staff_job_visibility",
      return_value=m,
  )


def test_job_plots_returns_loading_when_artifacts_missing():
  from hpcperfstats.site.lib.machine import api

  factory = RequestFactory()
  request = factory.get("/api/jobs/2945017/plots/")
  request.session = {"username": "alice", "is_staff": True}

  fake_job = SimpleNamespace(jid=2945017)

  with _patch_allow_job_visibility(), patch(
      "hpcperfstats.site.lib.machine.api._require_auth", return_value=None
  ), patch(
      "hpcperfstats.site.lib.machine.api.cached_orm", return_value=fake_job
  ), patch("hpcperfstats.site.lib.machine.api.cache") as mock_cache, patch(
      "hpcperfstats.site.lib.machine.api._get_small_executor"
  ) as mock_exec:
    mock_cache.get.return_value = None
    response = api.job_plots(request, 2945017)

  assert response.status_code == 202
  payload = response.data
  assert payload["status"] == "loading"
  assert payload["retry_after_seconds"] == 2
  mock_exec.assert_not_called()


def test_job_plots_returns_full_payload_from_l1_cache():
  from hpcperfstats.site.lib.machine import api

  factory = RequestFactory()
  request = factory.get("/api/jobs/2945017/plots/")
  request.session = {"username": "alice", "is_staff": True}

  fake_job = SimpleNamespace(jid=2945017)

  def _cache_get(cache_key, default=None):
    if "summary_plot" in str(cache_key):
      return {"plot_item": {"kind": "summary"}, "unavailable_reason": None}
    if "gpu_roofline" in str(cache_key):
      return {"plot_item": {"kind": "gpu_roofline"}, "unavailable_reason": None}
    if "roofline" in str(cache_key):
      return {"plot_item": {"kind": "roofline"}, "unavailable_reason": None}
    return default

  with _patch_allow_job_visibility(), patch(
      "hpcperfstats.site.lib.machine.api._require_auth", return_value=None
  ), patch(
      "hpcperfstats.site.lib.machine.api.cached_orm", return_value=fake_job
  ), patch("hpcperfstats.site.lib.machine.api.cache") as mock_cache:
    mock_cache.get.side_effect = _cache_get
    response = api.job_plots(request, 2945017)

  assert response.status_code == 200
  payload = response.data
  assert payload["mplot_item"] == {"kind": "summary"}
  assert payload["rplot_item"] == {"kind": "roofline"}
  assert payload["grplot_item"] == {"kind": "gpu_roofline"}
  assert payload["mplot_unavailable_reason"] is None
  assert payload["rplot_unavailable_reason"] is None
  assert payload["grplot_unavailable_reason"] is None


def test_job_plots_supports_per_plot_ready_from_l1():
  from hpcperfstats.site.lib.machine import api

  factory = RequestFactory()
  request = factory.get("/api/jobs/2945017/plots/?plot=summary_plot")
  request.session = {"username": "alice", "is_staff": True}

  fake_job = SimpleNamespace(jid=2945017)

  with _patch_allow_job_visibility(), patch(
      "hpcperfstats.site.lib.machine.api._require_auth", return_value=None
  ), patch(
      "hpcperfstats.site.lib.machine.api.cached_orm", return_value=fake_job
  ), patch("hpcperfstats.site.lib.machine.api.cache") as mock_cache:
    mock_cache.get.return_value = {
        "plot_item": {"kind": "summary"},
        "unavailable_reason": None,
    }
    response = api.job_plots(request, 2945017)

  assert response.status_code == 200
  assert response.data["status"] == "ready"
  assert response.data["plot"] == "summary_plot"
  assert response.data["plot_item"] == {"kind": "summary"}
  assert response.data["unavailable_reason"] is None


def test_job_plots_null_l1_entry_is_terminal_ready():
  """Null plot_item + reason on L1/L2 hit must not return loading."""
  from hpcperfstats.site.lib.machine import api

  factory = RequestFactory()
  request = factory.get("/api/jobs/2945017/plots/")
  request.session = {"username": "alice", "is_staff": True}

  fake_job = SimpleNamespace(jid=2945017)
  reason = "Missing roofline counters in host_data"

  def _cache_get(cache_key, default=None):
    key = str(cache_key)
    if "summary_plot" in key:
      return {"plot_item": {"kind": "summary"}, "unavailable_reason": None}
    if "gpu_roofline" in key:
      return {"plot_item": {"kind": "gpu_roofline"}, "unavailable_reason": None}
    if "roofline" in key:
      return {"plot_item": None, "unavailable_reason": reason}
    return default

  with _patch_allow_job_visibility(), patch(
      "hpcperfstats.site.lib.machine.api._require_auth", return_value=None
  ), patch(
      "hpcperfstats.site.lib.machine.api.cached_orm", return_value=fake_job
  ), patch("hpcperfstats.site.lib.machine.api.cache") as mock_cache:
    mock_cache.get.side_effect = _cache_get
    response = api.job_plots(request, 2945017)

  assert response.status_code == 200
  assert response.data["rplot_item"] is None
  assert response.data["rplot_unavailable_reason"] == reason


def test_job_plots_progressive_returns_200_partial_when_artifacts_missing():
  """progressive=1 returns 200 partial JSON (not 202) when artifacts are not ready."""
  from hpcperfstats.site.lib.machine import api

  factory = RequestFactory()
  request = factory.get("/api/jobs/2945017/plots/?progressive=1")
  request.session = {"username": "alice", "is_staff": True}

  fake_job = SimpleNamespace(jid=2945017)

  vis_qs = MagicMock()
  vis_qs.exists.return_value = True
  with patch("hpcperfstats.site.lib.machine.api._require_auth", return_value=None), patch(
      "hpcperfstats.site.lib.machine.api.cached_orm", return_value=fake_job
  ), patch(
      "hpcperfstats.site.lib.machine.api._apply_non_staff_job_visibility",
      return_value=vis_qs,
  ), patch("hpcperfstats.site.lib.machine.api.cache") as mock_cache, patch(
      "hpcperfstats.site.lib.machine.api._get_small_executor"
  ) as mock_exec:
    mock_cache.get.return_value = None
    response = api.job_plots(request, 2945017)

  assert response.status_code == 200
  payload = response.data
  assert payload["status"] == "partial"
  assert payload["progressive"] is True
  assert set(payload["loading_plots"]) == {
      "summary_plot",
      "roofline",
      "gpu_roofline",
  }
  assert "mplot_item" not in payload
  mock_exec.assert_not_called()


def test_job_plots_progressive_partial_includes_completed_plot_fields():
  """progressive partial responses embed finished plots and omit keys still loading."""
  from hpcperfstats.site.lib.machine import api

  factory = RequestFactory()
  request = factory.get("/api/jobs/2945017/plots/?progressive=1")
  request.session = {"username": "alice", "is_staff": True}

  fake_job = SimpleNamespace(jid=2945017)

  def _cache_get(cache_key, default=None):
    if "summary_plot" in str(cache_key):
      return {"plot_item": {"kind": "summary"}, "unavailable_reason": None}
    return default

  vis_qs = MagicMock()
  vis_qs.exists.return_value = True
  with patch("hpcperfstats.site.lib.machine.api._require_auth", return_value=None), patch(
      "hpcperfstats.site.lib.machine.api.cached_orm", return_value=fake_job
  ), patch(
      "hpcperfstats.site.lib.machine.api._apply_non_staff_job_visibility",
      return_value=vis_qs,
  ), patch("hpcperfstats.site.lib.machine.api.cache") as mock_cache:
    mock_cache.get.side_effect = _cache_get
    response = api.job_plots(request, 2945017)

  assert response.status_code == 200
  payload = response.data
  assert payload["status"] == "partial"
  assert payload["mplot_item"] == {"kind": "summary"}
  assert payload["mplot_unavailable_reason"] is None
  assert "rplot_item" not in payload
  assert "grplot_item" not in payload
  assert "roofline" in payload["loading_plots"]
  assert "gpu_roofline" in payload["loading_plots"]


def test_job_plots_progressive_final_payload_includes_ready_metadata():
  """When all plots are ready, progressive=1 adds status/loading_plots to the batch body."""
  from hpcperfstats.site.lib.machine import api

  factory = RequestFactory()
  request = factory.get("/api/jobs/2945017/plots/?progressive=1")
  request.session = {"username": "alice", "is_staff": True}

  fake_job = SimpleNamespace(jid=2945017)

  def _cache_get(cache_key, default=None):
    return {"plot_item": {"ok": True}, "unavailable_reason": None}

  vis_qs = MagicMock()
  vis_qs.exists.return_value = True
  with patch("hpcperfstats.site.lib.machine.api._require_auth", return_value=None), patch(
      "hpcperfstats.site.lib.machine.api.cached_orm", return_value=fake_job
  ), patch(
      "hpcperfstats.site.lib.machine.api._apply_non_staff_job_visibility",
      return_value=vis_qs,
  ), patch("hpcperfstats.site.lib.machine.api.cache") as mock_cache:
    mock_cache.get.side_effect = _cache_get
    response = api.job_plots(request, 2945017)

  assert response.status_code == 200
  payload = response.data
  assert payload["status"] == "ready"
  assert payload["progressive"] is True
  assert payload["loading_plots"] == []


def test_apply_zoom_layout_to_json_item_keeps_glyph_dimensions():
  """Zoom JSON transform should not clobber glyph width/height value specs."""
  from hpcperfstats.site.lib.machine.api import _apply_zoom_layout_to_json_item

  item = {
      "doc": {
          "roots": [
              {
                  "type": "object",
                  "name": "Figure",
                  "id": "fig-1",
                  "attributes": {"width": 400, "height": 200},
              },
              {
                  "type": "object",
                  "name": "Rect",
                  "id": "glyph-1",
                  "attributes": {
                      "width": {"type": "value", "value": 1},
                      "height": {"type": "value", "value": 1},
                  },
              },
          ]
      }
  }

  out = _apply_zoom_layout_to_json_item(item)
  roots = out["doc"]["roots"]
  fig_attrs = roots[0]["attributes"]
  rect_attrs = roots[1]["attributes"]

  assert fig_attrs["width"] is None
  assert fig_attrs["height"] is None
  assert fig_attrs["sizing_mode"] == "stretch_width"
  assert fig_attrs["width_policy"] == "max"
  assert fig_attrs["height_policy"] == "auto"
  assert rect_attrs["width"] == {"type": "value", "value": 1}
  assert rect_attrs["height"] == {"type": "value", "value": 1}


def test_apply_zoom_layout_to_json_item_does_not_mutate_document_config():
  """Zoom JSON transform must not inject unsupported attrs into DocumentConfig."""
  from hpcperfstats.site.lib.machine.api import _apply_zoom_layout_to_json_item

  item = {
      "doc": {
          "roots": [
              {
                  "type": "object",
                  "name": "DocumentConfig",
                  "id": "cfg-1",
                  "attributes": {"notifications": {"type": "value", "value": []}},
              }
          ]
      }
  }

  out = _apply_zoom_layout_to_json_item(item)
  cfg_attrs = out["doc"]["roots"][0]["attributes"]
  assert "sizing_mode" not in cfg_attrs
  assert "width_policy" not in cfg_attrs
  assert "height_policy" not in cfg_attrs
