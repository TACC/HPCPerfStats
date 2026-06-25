"""Tests for persisted job plot artifacts (gzip json_item + fingerprint)."""

import pytest
from datetime import timedelta
from django.utils import timezone

from hpcperfstats.site.lib.machine.cache_utils import invalidate_job_plot_cache_keys_for_jids
from hpcperfstats.site.lib.machine.job_plot_artifacts import (
    JOB_PLOT_KINDS,
    JOB_PLOT_LAYOUT_ZOOM_V3,
    PAYLOAD_ENCODING_GZIP_JSON,
    compute_plot_input_fingerprint,
    decompress_plot_item_dict,
    get_live_distinct_time_count_for_jid,
    json_item_to_compressed_payload,
    load_cached_job_plot_entry,
    upsert_job_plot_artifact_batch,
    upsert_job_plot_artifact,
    persist_job_plot_artifacts_for_jid,
)
from hpcperfstats.site.lib.machine.job_detail_artifacts import (
    ARTIFACT_KIND_JOB_DETAIL,
    upsert_job_detail_artifact,
)
from hpcperfstats.site.lib.machine.models import job_data, job_detail_artifact, job_plot_artifact


@pytest.mark.django_db
def test_json_item_to_compressed_payload_roundtrip():
  big = {
      "roots": list(range(500)),
      "nested": {"x": [[1.5] * 200] * 50},
  }
  raw, compressed, enc = json_item_to_compressed_payload(big)
  assert enc == PAYLOAD_ENCODING_GZIP_JSON
  assert len(compressed) < len(raw)
  out = decompress_plot_item_dict(compressed, enc)
  assert out == big


def test_decompress_plot_item_dict_rejects_unknown_encoding():
  with pytest.raises(ValueError, match="Unsupported"):
    decompress_plot_item_dict(b"x", "unknown_codec")


@pytest.mark.django_db
def test_fingerprint_stable_for_same_job_fields():
  now = timezone.now()
  j = job_data.objects.create(
      jid="fpjob1",
      submit_time=now,
      start_time=now,
      end_time=now,
      username="u1",
      host_list=["n1", "n2"],
      metrics_distinct_time_count=42,
  )
  fp1 = compute_plot_input_fingerprint(j, 99)
  fp2 = compute_plot_input_fingerprint(j, 99)
  assert fp1 == fp2
  j.refresh_from_db()
  j.host_list = ["n1", "n2", "n3"]
  j.save(update_fields=["host_list"])
  fp3 = compute_plot_input_fingerprint(j, 100)
  assert fp3 != fp1


@pytest.mark.django_db
def test_fingerprint_changes_when_telemetry_first_time_moves():

  start = timezone.now() - timedelta(hours=2)
  end = timezone.now()
  j = job_data.objects.create(
      jid="fptel1",
      submit_time=start,
      start_time=start,
      end_time=end,
      username="u1",
      host_list=["n1"],
      metrics_distinct_time_count=5,
      telemetry_first_time=start + timedelta(minutes=5),
      telemetry_last_time=end - timedelta(minutes=5),
  )
  fp1 = compute_plot_input_fingerprint(j, 5)
  j.telemetry_first_time = start + timedelta(minutes=1)
  j.save(update_fields=["telemetry_first_time"])
  fp2 = compute_plot_input_fingerprint(j, 5)
  assert fp2 != fp1


@pytest.mark.machine_unit_mock
def test_app_plot_artifact_schema_version_bumped_for_telemetry_bounds():
  from hpcperfstats.site.lib.machine import job_plot_artifacts as plot_cfg

  assert plot_cfg.APP_PLOT_ARTIFACT_SCHEMA_VERSION == 10


@pytest.mark.django_db
def test_load_cached_job_plot_entry_miss_and_hit():
  now = timezone.now()
  j = job_data.objects.create(
      jid="pcache1",
      submit_time=now,
      start_time=now,
      end_time=now,
      username="u1",
      host_list=["n1"],
      metrics_distinct_time_count=1,
  )
  live = get_live_distinct_time_count_for_jid("pcache1")
  fp = compute_plot_input_fingerprint(j, live)
  assert (
      load_cached_job_plot_entry("pcache1", "summary_plot", "normal", fp) is None
  )
  item = {"doc": {"roots": []}, "root_ids": []}
  upsert_job_plot_artifact("pcache1", "summary_plot", "normal", fp, item)
  entry = load_cached_job_plot_entry("pcache1", "summary_plot", "normal", fp)
  assert entry is not None
  assert entry["plot_item"] == item
  assert entry["unavailable_reason"] is None


@pytest.mark.django_db
def test_load_cached_job_plot_entry_stale_fingerprint():
  now = timezone.now()
  j = job_data.objects.create(
      jid="stale1",
      submit_time=now,
      start_time=now,
      end_time=now,
      username="u1",
      host_list=["n1"],
      metrics_distinct_time_count=1,
  )
  fp_old = compute_plot_input_fingerprint(j, 1)
  upsert_job_plot_artifact(
      "stale1", "roofline", "normal", fp_old, {"a": 1}
  )
  j.host_list = ["n1", "n2"]
  j.save(update_fields=["host_list"])
  fp_new = compute_plot_input_fingerprint(j, 999)
  assert (
      load_cached_job_plot_entry("stale1", "roofline", "normal", fp_new) is None
  )


@pytest.mark.django_db
def test_invalidate_job_plot_cache_keys_for_jids_deletes_rows():
  now = timezone.now()
  j = job_data.objects.create(
      jid="inv1",
      submit_time=now,
      start_time=now,
      end_time=now,
      username="u1",
      host_list=["n1"],
      metrics_distinct_time_count=0,
  )
  fp = compute_plot_input_fingerprint(j, 0)
  upsert_job_plot_artifact("inv1", "roofline", "normal", fp, {"x": 1})
  upsert_job_detail_artifact("inv1", ARTIFACT_KIND_JOB_DETAIL, "", "fp", {"x": 1})
  assert job_plot_artifact.objects.filter(jid_id="inv1").count() == 1
  assert job_detail_artifact.objects.filter(jid_id="inv1").count() == 1
  invalidate_job_plot_cache_keys_for_jids(["inv1"])
  assert job_plot_artifact.objects.filter(jid_id="inv1").count() == 0
  assert job_detail_artifact.objects.filter(jid_id="inv1").count() == 0


@pytest.mark.django_db
def test_load_cached_job_plot_entry_zoom_uses_normal_layout_fallback():
  now = timezone.now()
  j = job_data.objects.create(
      jid="zoomfb1",
      submit_time=now,
      start_time=now,
      end_time=now,
      username="u1",
      host_list=["n1"],
      metrics_distinct_time_count=1,
  )
  fp = compute_plot_input_fingerprint(j, 1)
  normal_item = {
      "doc": {
          "roots": [
              {
                  "type": "object",
                  "name": "Figure",
                  "id": "fig-1",
                  "attributes": {"width": 300, "height": 120},
              }
          ]
      }
  }
  upsert_job_plot_artifact("zoomfb1", "summary_plot", "normal", fp, normal_item)

  entry = load_cached_job_plot_entry(
      "zoomfb1",
      "summary_plot",
      JOB_PLOT_LAYOUT_ZOOM_V3,
      fp,
  )
  assert entry is not None
  assert entry["unavailable_reason"] is None
  assert entry["plot_item"]["doc"]["roots"][0]["attributes"]["sizing_mode"] == "stretch_width"


@pytest.mark.django_db
def test_load_cached_job_plot_entry_returns_none_on_corrupt_payload():
  now = timezone.now()
  j = job_data.objects.create(
      jid="corrupt1",
      submit_time=now,
      start_time=now,
      end_time=now,
      username="u1",
      host_list=["n1"],
      metrics_distinct_time_count=1,
  )
  fp = compute_plot_input_fingerprint(j, 1)
  upsert_job_plot_artifact("corrupt1", "roofline", "normal", fp, {"ok": True})

  row = job_plot_artifact.objects.get(jid_id="corrupt1", plot_kind="roofline", layout="normal")
  row.payload_compressed = b"definitely-not-gzip"
  row.save(update_fields=["payload_compressed"])

  assert load_cached_job_plot_entry("corrupt1", "roofline", "normal", fp) is None


@pytest.mark.django_db
def test_upsert_job_plot_artifact_batch_updates_existing_row():
  now = timezone.now()
  j = job_data.objects.create(
      jid="batch1",
      submit_time=now,
      start_time=now,
      end_time=now,
      username="u1",
      host_list=["n1"],
      metrics_distinct_time_count=1,
  )
  fp = compute_plot_input_fingerprint(j, 1)
  upsert_job_plot_artifact_batch([
      ("batch1", "summary_plot", "normal", fp, {"v": 1}),
      ("batch1", "roofline", "normal", fp, {"r": 1}),
  ])
  assert job_plot_artifact.objects.filter(jid_id="batch1").count() == 2
  upsert_job_plot_artifact_batch([
      ("batch1", "summary_plot", "normal", fp, {"v": 2}),
  ])
  row = job_plot_artifact.objects.get(
      jid_id="batch1", plot_kind="summary_plot", layout="normal")
  out = decompress_plot_item_dict(bytes(row.payload_compressed), row.payload_encoding)
  assert out == {"plot_item": {"v": 2}, "unavailable_reason": None}


@pytest.mark.django_db
def test_persist_job_plot_artifacts_persists_fresh_unavailable_rows(monkeypatch):
  now = timezone.now()
  job = job_data.objects.create(
      jid="plotunavail1",
      submit_time=now,
      start_time=now,
      end_time=now,
      username="u1",
      host_list=["n1"],
      metrics_distinct_time_count=1,
  )

  class _FakeJt:
    host_list = ["n1"]
    acct_host_list = ["n1"]

    def get_host_time_df(self):
      import pandas as pd
      return pd.DataFrame({"host": ["n1"], "time": [now]})

    def get_aggregate_df(self, _typ, _metric_column, _events, _conv):
      import pandas as pd
      return pd.DataFrame({"host": ["n1"], "time": [now], "sum_val": [0.0]})

  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_plot_artifacts.get_live_distinct_time_count_for_jid",
      lambda jid: 1,
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_plot_artifacts.jid_table.jid_table",
      lambda jid: _FakeJt(),
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_plot_artifacts.compute_plot_item_for_kind",
      lambda _jt, _kind, _zoom_mode: (None, "No plot data for this job."),
  )

  persist_job_plot_artifacts_for_jid(job.jid)

  fp = compute_plot_input_fingerprint(job, 1)
  entry = load_cached_job_plot_entry(job.jid, "summary_plot", "normal", fp)
  assert entry is not None
  assert entry["plot_item"] is None
  assert entry["unavailable_reason"] == "No plot data for this job."
  assert job_plot_artifact.objects.filter(jid_id=job.jid).count() == len(JOB_PLOT_KINDS)


@pytest.mark.django_db
def test_persist_job_plot_artifacts_marks_plot_exceptions_unavailable(monkeypatch):
  now = timezone.now()
  job = job_data.objects.create(
      jid="ploterror1",
      submit_time=now,
      start_time=now,
      end_time=now,
      username="u1",
      host_list=["n1"],
      metrics_distinct_time_count=1,
  )

  class _FakeJt:
    host_list = ["n1"]
    acct_host_list = ["n1"]

    def get_host_time_df(self):
      import pandas as pd
      return pd.DataFrame({"host": ["n1"], "time": [now]})

    def get_aggregate_df(self, _typ, _metric_column, _events, _conv):
      import pandas as pd
      return pd.DataFrame({"host": ["n1"], "time": [now], "sum_val": [0.0]})

  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_plot_artifacts.get_live_distinct_time_count_for_jid",
      lambda jid: 1,
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_plot_artifacts.jid_table.jid_table",
      lambda jid: _FakeJt(),
  )

  def _raise_plot_error(_jt, _kind, _zoom_mode):
    raise RuntimeError("boom")

  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_plot_artifacts.compute_plot_item_for_kind",
      _raise_plot_error,
  )

  persist_job_plot_artifacts_for_jid(job.jid)

  fp = compute_plot_input_fingerprint(job, 1)
  entry = load_cached_job_plot_entry(job.jid, "summary_plot", "normal", fp)
  assert entry is not None
  assert entry["plot_item"] is None
  assert entry["unavailable_reason"] == "Plot generation failed during artifact prewarm."


@pytest.mark.django_db
def test_persist_job_plot_artifacts_reuses_context_rows_map(monkeypatch):
  now = timezone.now()
  job_data.objects.create(
      jid="ctxrows1",
      submit_time=now,
      start_time=now,
      end_time=now,
      username="u1",
      host_list=["n1"],
      metrics_distinct_time_count=1,
  )
  calls = {"rows": 0}
  shared = {"_telemetry": {}}

  class _FakeJt:
    host_list = ["n1"]
    acct_host_list = ["n1"]

    def get_host_time_df(self):
      import pandas as pd
      return pd.DataFrame({"host": ["n1"], "time": [now]})

    def get_aggregate_df(self, _typ, _metric_column, _events, _conv):
      import pandas as pd
      return pd.DataFrame({"host": ["n1"], "time": [now], "sum_val": [1.0]})

  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_plot_artifacts.get_live_distinct_time_count_for_jid",
      lambda jid: 1,
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_plot_artifacts.jid_table.jid_table",
      lambda jid: _FakeJt(),
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_plot_artifacts._load_rows_map",
      lambda jid, layouts: calls.__setitem__("rows", calls["rows"] + 1) or {},
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_plot_artifacts.compute_plot_item_for_kind",
      lambda j, kind, zoom_mode: ({"k": kind, "z": zoom_mode}, None),
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_plot_artifacts.upsert_job_plot_artifact_batch",
      lambda rows: None,
  )

  persist_job_plot_artifacts_for_jid("ctxrows1", context=shared)
  persist_job_plot_artifacts_for_jid("ctxrows1", context=shared)

  assert calls["rows"] == 1
  assert int(shared["_telemetry"].get("plot_row_lookup_queries", 0)) == 1


@pytest.mark.django_db
def test_jt_memo_proxy_telemetry_counts_hits():
  from hpcperfstats.site.lib.machine.job_plot_artifacts import _JtMemoProxy
  import pandas as pd

  class _FakeJt:
    def __init__(self):
      self.host_calls = 0
      self.agg_calls = 0

    def get_host_time_df(self):
      self.host_calls += 1
      return pd.DataFrame({"host": ["n1"], "time": [timezone.now()]})

    def get_aggregate_df(self, _typ, _metric_column, _events, _conv):
      self.agg_calls += 1
      return pd.DataFrame({"host": ["n1"], "time": [timezone.now()], "sum_val": [1.0]})

  telemetry = {}
  proxy = _JtMemoProxy(_FakeJt(), telemetry=telemetry)
  proxy.get_host_time_df()
  proxy.get_host_time_df()
  proxy.get_aggregate_df("t", "arc", ["a"], 1.0)
  proxy.get_aggregate_df("t", "arc", ["a"], 1.0)
  proxy.get_aggregate_df("t", "arc", ["b", "a"], 1.0)
  proxy.get_aggregate_df("t", "arc", ["a", "b"], 1.0)
  assert telemetry["plot_jt_memo_host_time_hits"] == 1
  assert telemetry["plot_jt_memo_aggregate_misses"] == 2
  assert telemetry["plot_jt_memo_aggregate_hits"] == 2
