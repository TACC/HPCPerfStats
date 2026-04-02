from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from django.test import RequestFactory


def _patch_allow_job_visibility():
  """job_plots calls _apply_non_staff_job_visibility(...).exists() before other mocks apply."""
  m = MagicMock()
  m.exists.return_value = True
  return patch(
      "hpcperfstats.site.machine.api._apply_non_staff_job_visibility",
      return_value=m,
  )


def test_job_plots_returns_loading_while_background_tasks_are_pending():
  from hpcperfstats.site.machine import api

  api._job_plot_inflight.clear()
  factory = RequestFactory()
  request = factory.get("/api/jobs/2945017/plots/")
  request.session = {"username": "alice", "is_staff": True}

  fake_job = SimpleNamespace(jid=2945017)

  class _FakeExecutor:
    def submit(self, fn):
      # Keep task submission shape realistic while avoiding real thread work.
      fut = Future()
      return fut

  with _patch_allow_job_visibility(), patch(
      "hpcperfstats.site.machine.api._require_auth", return_value=None
  ), patch(
      "hpcperfstats.site.machine.api.cached_orm", return_value=fake_job
  ), patch("hpcperfstats.site.machine.api.cache") as mock_cache, patch(
      "hpcperfstats.site.machine.api.jid_table.jid_table",
      return_value=SimpleNamespace(),
  ), patch(
      "hpcperfstats.site.machine.api._get_small_executor",
      return_value=_FakeExecutor(),
  ), patch(
      "hpcperfstats.site.machine.api.logging.getLogger",
      return_value=MagicMock(),
  ):
    mock_cache.get.return_value = None
    response = api.job_plots(request, 2945017)

  assert response.status_code == 202
  payload = response.data
  assert payload["status"] == "loading"
  assert payload["retry_after_seconds"] == 2


def test_evict_stale_inflight_plot_tasks_removes_hung_entries():
  from hpcperfstats.site.machine import api

  api._job_plot_inflight.clear()
  stale_future = Future()
  done_future = Future()
  done_future.set_result(("ok", None))
  api._job_plot_inflight[("j1", "summary_plot", "normal")] = {
      "future": stale_future,
      "created_at": 10.0,
  }
  api._job_plot_inflight[("j2", "heatmap", "normal")] = {
      "future": done_future,
      "created_at": 20.0,
  }
  api._job_plot_inflight[("j3", "roofline", "normal")] = {
      "future": Future(),
      "created_at": 300.0,
  }

  with patch("hpcperfstats.site.machine.api.time.monotonic", return_value=400.0):
    api._evict_stale_inflight_plot_tasks()

  assert ("j1", "summary_plot", "normal") not in api._job_plot_inflight
  assert ("j2", "heatmap", "normal") not in api._job_plot_inflight
  assert ("j3", "roofline", "normal") in api._job_plot_inflight


def test_job_plots_returns_full_payload_after_loading_state():
  from hpcperfstats.site.machine import api

  api._job_plot_inflight.clear()
  factory = RequestFactory()
  request = factory.get("/api/jobs/2945017/plots/")
  request.session = {"username": "alice", "is_staff": True}

  fake_job = SimpleNamespace(jid=2945017)
  summary_future = Future()
  heatmap_future = Future()
  roofline_future = Future()
  gpu_roofline_future = Future()
  submitted_futures = [summary_future, heatmap_future, roofline_future, gpu_roofline_future]

  class _FakeExecutor:
    def submit(self, fn):
      return submitted_futures.pop(0)

  with _patch_allow_job_visibility(), patch(
      "hpcperfstats.site.machine.api._require_auth", return_value=None
  ), patch(
      "hpcperfstats.site.machine.api.cached_orm", return_value=fake_job
  ), patch("hpcperfstats.site.machine.api.cache") as mock_cache, patch(
      "hpcperfstats.site.machine.api.jid_table.jid_table",
      return_value=SimpleNamespace(),
  ), patch(
      "hpcperfstats.site.machine.api._get_small_executor",
      return_value=_FakeExecutor(),
  ), patch(
      "hpcperfstats.site.machine.api.logging.getLogger",
      return_value=MagicMock(),
  ):
    mock_cache.get.return_value = None

    first_response = api.job_plots(request, 2945017)
    assert first_response.status_code == 202
    assert first_response.data["status"] == "loading"

    summary_future.set_result(({"kind": "summary"}, None))
    heatmap_future.set_result(({"kind": "heatmap"}, None))
    roofline_future.set_result(({"kind": "roofline"}, None))
    gpu_roofline_future.set_result(({"kind": "gpu_roofline"}, None))

    second_response = api.job_plots(request, 2945017)

  assert second_response.status_code == 200
  payload = second_response.data
  assert payload["mplot_item"] == {"kind": "summary"}
  assert payload["hplot_item"] == {"kind": "heatmap"}
  assert payload["rplot_item"] == {"kind": "roofline"}
  assert payload["grplot_item"] == {"kind": "gpu_roofline"}
  assert payload["mplot_unavailable_reason"] is None
  assert payload["hplot_unavailable_reason"] is None
  assert payload["rplot_unavailable_reason"] is None
  assert payload["grplot_unavailable_reason"] is None


def test_job_plots_supports_per_plot_loading_and_ready_states():
  from hpcperfstats.site.machine import api

  api._job_plot_inflight.clear()
  factory = RequestFactory()
  request = factory.get("/api/jobs/2945017/plots/?plot=summary_plot")
  request.session = {"username": "alice", "is_staff": True}

  fake_job = SimpleNamespace(jid=2945017)
  summary_future = Future()

  class _FakeExecutor:
    def submit(self, fn):
      return summary_future

  with _patch_allow_job_visibility(), patch(
      "hpcperfstats.site.machine.api._require_auth", return_value=None
  ), patch(
      "hpcperfstats.site.machine.api.cached_orm", return_value=fake_job
  ), patch("hpcperfstats.site.machine.api.cache") as mock_cache, patch(
      "hpcperfstats.site.machine.api.jid_table.jid_table",
      return_value=SimpleNamespace(),
  ), patch(
      "hpcperfstats.site.machine.api._get_small_executor",
      return_value=_FakeExecutor(),
  ), patch(
      "hpcperfstats.site.machine.api.logging.getLogger",
      return_value=MagicMock(),
  ):
    mock_cache.get.return_value = None

    loading_response = api.job_plots(request, 2945017)
    assert loading_response.status_code == 202
    assert loading_response.data["status"] == "loading"
    assert loading_response.data["loading_plots"] == ["summary_plot"]

    summary_future.set_result(({"kind": "summary"}, None))
    ready_response = api.job_plots(request, 2945017)

  assert ready_response.status_code == 200
  assert ready_response.data["status"] == "ready"
  assert ready_response.data["plot"] == "summary_plot"
  assert ready_response.data["plot_item"] == {"kind": "summary"}
  assert ready_response.data["unavailable_reason"] is None


def test_job_plots_refreshes_stale_cached_generic_heatmap_reason():
  from hpcperfstats.site.machine import api

  api._job_plot_inflight.clear()
  factory = RequestFactory()
  request = factory.get("/api/jobs/2945017/plots/")
  request.session = {"username": "alice", "is_staff": True}

  fake_job = SimpleNamespace(jid=2945017)
  heatmap_future = Future()

  class _FakeExecutor:
    def submit(self, fn):
      return heatmap_future

  def _cache_get(cache_key):
    if "heatmap" in cache_key:
      return {
          "plot_item": None,
          "unavailable_reason": "No host-level MSR data available",
      }
    if "summary_plot" in cache_key:
      return {"plot_item": {"kind": "summary"}, "unavailable_reason": None}
    if "gpu_roofline" in cache_key:
      return {"plot_item": {"kind": "gpu_roofline"}, "unavailable_reason": None}
    if "roofline" in cache_key:
      return {"plot_item": {"kind": "roofline"}, "unavailable_reason": None}
    return None

  with _patch_allow_job_visibility(), patch(
      "hpcperfstats.site.machine.api._require_auth", return_value=None
  ), patch(
      "hpcperfstats.site.machine.api.cached_orm", return_value=fake_job
  ), patch("hpcperfstats.site.machine.api.cache") as mock_cache, patch(
      "hpcperfstats.site.machine.api.jid_table.jid_table",
      return_value=SimpleNamespace(),
  ), patch(
      "hpcperfstats.site.machine.api._get_small_executor",
      return_value=_FakeExecutor(),
  ), patch(
      "hpcperfstats.site.machine.api.logging.getLogger",
      return_value=MagicMock(),
  ):
    mock_cache.get.side_effect = _cache_get

    first_response = api.job_plots(request, 2945017)
    assert first_response.status_code == 202
    assert first_response.data["loading_plots"] == ["heatmap"]

    heatmap_future.set_result((None, "Missing CPI counters in host_data"))
    second_response = api.job_plots(request, 2945017)

  assert second_response.status_code == 200
  assert second_response.data["hplot_item"] is None
  assert second_response.data["hplot_unavailable_reason"] == "Missing CPI counters in host_data"


def test_job_plots_refreshes_stale_cached_generic_roofline_reason():
  from hpcperfstats.site.machine import api

  api._job_plot_inflight.clear()
  factory = RequestFactory()
  request = factory.get("/api/jobs/2945017/plots/")
  request.session = {"username": "alice", "is_staff": True}

  fake_job = SimpleNamespace(jid=2945017)
  roofline_future = Future()

  class _FakeExecutor:
    def submit(self, fn):
      return roofline_future

  def _cache_get(cache_key):
    if "summary_plot" in cache_key:
      return {"plot_item": {"kind": "summary"}, "unavailable_reason": None}
    if "heatmap" in cache_key:
      return {"plot_item": {"kind": "heatmap"}, "unavailable_reason": None}
    if "gpu_roofline" in cache_key:
      return {"plot_item": {"kind": "gpu_roofline"}, "unavailable_reason": None}
    if "roofline" in cache_key:
      return {
          "plot_item": None,
          "unavailable_reason": "No FLOPS/memory bandwidth data available for roofline.",
      }
    return None

  with _patch_allow_job_visibility(), patch(
      "hpcperfstats.site.machine.api._require_auth", return_value=None
  ), patch(
      "hpcperfstats.site.machine.api.cached_orm", return_value=fake_job
  ), patch("hpcperfstats.site.machine.api.cache") as mock_cache, patch(
      "hpcperfstats.site.machine.api.jid_table.jid_table",
      return_value=SimpleNamespace(),
  ), patch(
      "hpcperfstats.site.machine.api._get_small_executor",
      return_value=_FakeExecutor(),
  ), patch(
      "hpcperfstats.site.machine.api.logging.getLogger",
      return_value=MagicMock(),
  ):
    mock_cache.get.side_effect = _cache_get

    first_response = api.job_plots(request, 2945017)
    assert first_response.status_code == 202
    assert first_response.data["loading_plots"] == ["roofline"]

    roofline_future.set_result((None, "Missing roofline counters in host_data"))
    second_response = api.job_plots(request, 2945017)

  assert second_response.status_code == 200
  assert second_response.data["rplot_item"] is None
  assert second_response.data["rplot_unavailable_reason"] == "Missing roofline counters in host_data"


def test_job_plots_refreshes_stale_cached_generic_summary_reason():
  from hpcperfstats.site.machine import api

  api._job_plot_inflight.clear()
  factory = RequestFactory()
  request = factory.get("/api/jobs/2945017/plots/")
  request.session = {"username": "alice", "is_staff": True}

  fake_job = SimpleNamespace(jid=2945017)
  summary_future = Future()

  class _FakeExecutor:
    def submit(self, fn):
      return summary_future

  def _cache_get(cache_key):
    if "summary_plot" in cache_key:
      return {
          "plot_item": None,
          "unavailable_reason": "No metric data available for this job.",
      }
    if "heatmap" in cache_key:
      return {"plot_item": {"kind": "heatmap"}, "unavailable_reason": None}
    if "gpu_roofline" in cache_key:
      return {"plot_item": {"kind": "gpu_roofline"}, "unavailable_reason": None}
    if "roofline" in cache_key:
      return {"plot_item": {"kind": "roofline"}, "unavailable_reason": None}
    return None

  with _patch_allow_job_visibility(), patch(
      "hpcperfstats.site.machine.api._require_auth", return_value=None
  ), patch(
      "hpcperfstats.site.machine.api.cached_orm", return_value=fake_job
  ), patch("hpcperfstats.site.machine.api.cache") as mock_cache, patch(
      "hpcperfstats.site.machine.api.jid_table.jid_table",
      return_value=SimpleNamespace(),
  ), patch(
      "hpcperfstats.site.machine.api._get_small_executor",
      return_value=_FakeExecutor(),
  ), patch(
      "hpcperfstats.site.machine.api.logging.getLogger",
      return_value=MagicMock(),
  ):
    mock_cache.get.side_effect = _cache_get

    first_response = api.job_plots(request, 2945017)
    assert first_response.status_code == 202
    assert first_response.data["loading_plots"] == ["summary_plot"]

    summary_future.set_result((None, "Missing summary counters in host_data"))
    second_response = api.job_plots(request, 2945017)

  assert second_response.status_code == 200
  assert second_response.data["mplot_item"] is None
  assert second_response.data["mplot_unavailable_reason"] == "Missing summary counters in host_data"


def test_job_plots_refreshes_stale_cached_generic_gpu_roofline_reason():
  from hpcperfstats.site.machine import api

  api._job_plot_inflight.clear()
  factory = RequestFactory()
  request = factory.get("/api/jobs/2945017/plots/")
  request.session = {"username": "alice", "is_staff": True}

  fake_job = SimpleNamespace(jid=2945017)
  gpu_roofline_future = Future()

  class _FakeExecutor:
    def submit(self, fn):
      return gpu_roofline_future

  def _cache_get(cache_key):
    if "summary_plot" in cache_key:
      return {"plot_item": {"kind": "summary"}, "unavailable_reason": None}
    if "heatmap" in cache_key:
      return {"plot_item": {"kind": "heatmap"}, "unavailable_reason": None}
    if "gpu_roofline" in cache_key:
      return {
          "plot_item": None,
          "unavailable_reason": "No FLOPS/memory bandwidth data available for roofline.",
      }
    if "roofline" in cache_key:
      return {"plot_item": {"kind": "roofline"}, "unavailable_reason": None}
    return None

  with _patch_allow_job_visibility(), patch(
      "hpcperfstats.site.machine.api._require_auth", return_value=None
  ), patch(
      "hpcperfstats.site.machine.api.cached_orm", return_value=fake_job
  ), patch("hpcperfstats.site.machine.api.cache") as mock_cache, patch(
      "hpcperfstats.site.machine.api.jid_table.jid_table",
      return_value=SimpleNamespace(),
  ), patch(
      "hpcperfstats.site.machine.api._get_small_executor",
      return_value=_FakeExecutor(),
  ), patch(
      "hpcperfstats.site.machine.api.logging.getLogger",
      return_value=MagicMock(),
  ):
    mock_cache.get.side_effect = _cache_get

    first_response = api.job_plots(request, 2945017)
    assert first_response.status_code == 202
    assert first_response.data["loading_plots"] == ["gpu_roofline"]

    gpu_roofline_future.set_result((None, "Missing strict GPU roofline counters in host_data"))
    second_response = api.job_plots(request, 2945017)

  assert second_response.status_code == 200
  assert second_response.data["grplot_item"] is None
  assert (
      second_response.data["grplot_unavailable_reason"]
      == "Missing strict GPU roofline counters in host_data"
  )


def test_job_plots_progressive_returns_200_partial_while_tasks_pending():
  """progressive=1 returns 200 partial JSON (not 202) when plots are still running."""
  from hpcperfstats.site.machine import api

  api._job_plot_inflight.clear()
  factory = RequestFactory()
  request = factory.get("/api/jobs/2945017/plots/?progressive=1")
  request.session = {"username": "alice", "is_staff": True}

  fake_job = SimpleNamespace(jid=2945017)

  class _FakeExecutor:
    def submit(self, fn):
      fut = Future()
      return fut

  vis_qs = MagicMock()
  vis_qs.exists.return_value = True
  with patch("hpcperfstats.site.machine.api._require_auth", return_value=None), patch(
      "hpcperfstats.site.machine.api.cached_orm", return_value=fake_job
  ), patch(
      "hpcperfstats.site.machine.api._apply_non_staff_job_visibility",
      return_value=vis_qs,
  ), patch("hpcperfstats.site.machine.api.cache") as mock_cache, patch(
      "hpcperfstats.site.machine.api.jid_table.jid_table",
      return_value=SimpleNamespace(),
  ), patch(
      "hpcperfstats.site.machine.api._get_small_executor",
      return_value=_FakeExecutor(),
  ), patch(
      "hpcperfstats.site.machine.api.logging.getLogger",
      return_value=MagicMock(),
  ):
    mock_cache.get.return_value = None
    response = api.job_plots(request, 2945017)

  assert response.status_code == 200
  payload = response.data
  assert payload["status"] == "partial"
  assert payload["progressive"] is True
  assert set(payload["loading_plots"]) == {
      "summary_plot",
      "heatmap",
      "roofline",
      "gpu_roofline",
  }
  assert "mplot_item" not in payload


def test_job_plots_progressive_partial_includes_completed_plot_fields():
  """progressive partial responses embed finished plots and omit keys still loading."""
  from hpcperfstats.site.machine import api

  api._job_plot_inflight.clear()
  factory = RequestFactory()
  request = factory.get("/api/jobs/2945017/plots/?progressive=1")
  request.session = {"username": "alice", "is_staff": True}

  fake_job = SimpleNamespace(jid=2945017)
  summary_future = Future()
  summary_future.set_result(({"kind": "summary"}, None))
  heat_future = Future()
  roof_future = Future()
  gpu_future = Future()
  queue = iter([summary_future, heat_future, roof_future, gpu_future])

  class _FakeExecutor:
    def submit(self, fn):
      return next(queue)

  vis_qs = MagicMock()
  vis_qs.exists.return_value = True
  with patch("hpcperfstats.site.machine.api._require_auth", return_value=None), patch(
      "hpcperfstats.site.machine.api.cached_orm", return_value=fake_job
  ), patch(
      "hpcperfstats.site.machine.api._apply_non_staff_job_visibility",
      return_value=vis_qs,
  ), patch("hpcperfstats.site.machine.api.cache") as mock_cache, patch(
      "hpcperfstats.site.machine.api.jid_table.jid_table",
      return_value=SimpleNamespace(),
  ), patch(
      "hpcperfstats.site.machine.api._get_small_executor",
      return_value=_FakeExecutor(),
  ), patch(
      "hpcperfstats.site.machine.api.logging.getLogger",
      return_value=MagicMock(),
  ):
    mock_cache.get.return_value = None
    response = api.job_plots(request, 2945017)

  assert response.status_code == 200
  payload = response.data
  assert payload["status"] == "partial"
  assert payload["mplot_item"] == {"kind": "summary"}
  assert payload["mplot_unavailable_reason"] is None
  assert "hplot_item" not in payload
  assert "heatmap" in payload["loading_plots"]


def test_job_plots_progressive_final_payload_includes_ready_metadata():
  """When all plots are ready, progressive=1 adds status/loading_plots to the batch body."""
  from hpcperfstats.site.machine import api

  api._job_plot_inflight.clear()
  factory = RequestFactory()
  request = factory.get("/api/jobs/2945017/plots/?progressive=1")
  request.session = {"username": "alice", "is_staff": True}

  fake_job = SimpleNamespace(jid=2945017)
  futures = [Future() for _ in range(4)]
  for fut in futures:
    fut.set_result(({"ok": True}, None))

  class _FakeExecutor:
    def submit(self, fn):
      return futures.pop(0)

  vis_qs = MagicMock()
  vis_qs.exists.return_value = True
  with patch("hpcperfstats.site.machine.api._require_auth", return_value=None), patch(
      "hpcperfstats.site.machine.api.cached_orm", return_value=fake_job
  ), patch(
      "hpcperfstats.site.machine.api._apply_non_staff_job_visibility",
      return_value=vis_qs,
  ), patch("hpcperfstats.site.machine.api.cache") as mock_cache, patch(
      "hpcperfstats.site.machine.api.jid_table.jid_table",
      return_value=SimpleNamespace(),
  ), patch(
      "hpcperfstats.site.machine.api._get_small_executor",
      return_value=_FakeExecutor(),
  ), patch(
      "hpcperfstats.site.machine.api.logging.getLogger",
      return_value=MagicMock(),
  ):
    mock_cache.get.return_value = None
    response = api.job_plots(request, 2945017)

  assert response.status_code == 200
  payload = response.data
  assert payload["status"] == "ready"
  assert payload["progressive"] is True
  assert payload["loading_plots"] == []


def test_apply_zoom_layout_to_json_item_keeps_glyph_dimensions():
  """Zoom JSON transform should not clobber glyph width/height value specs."""
  from hpcperfstats.site.machine.api import _apply_zoom_layout_to_json_item

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
  assert rect_attrs["width"] == {"type": "value", "value": 1}
  assert rect_attrs["height"] == {"type": "value", "value": 1}


def test_apply_zoom_layout_to_json_item_does_not_mutate_document_config():
  """Zoom JSON transform must not inject unsupported attrs into DocumentConfig."""
  from hpcperfstats.site.machine.api import _apply_zoom_layout_to_json_item

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

