"""Tests for persisted job plot artifacts (gzip json_item + fingerprint)."""
import gzip
import json

import pytest
from django.utils import timezone

from hpcperfstats.site.machine.cache_utils import invalidate_job_plot_cache_keys_for_jids
from hpcperfstats.site.machine.job_plot_artifacts import (
    JOB_PLOT_LAYOUT_ZOOM_V3,
    PAYLOAD_ENCODING_GZIP_JSON,
    compute_plot_input_fingerprint,
    decompress_plot_item_dict,
    get_live_distinct_time_count_for_jid,
    json_item_to_compressed_payload,
    load_cached_job_plot_entry,
    upsert_job_plot_artifact,
)
from hpcperfstats.site.machine.models import job_data, job_plot_artifact


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
  fp3 = compute_plot_input_fingerprint(j, 100)
  assert fp3 != fp1


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
      "stale1", "heatmap", "normal", fp_old, {"a": 1}
  )
  fp_new = compute_plot_input_fingerprint(j, 999)
  assert (
      load_cached_job_plot_entry("stale1", "heatmap", "normal", fp_new) is None
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
  assert job_plot_artifact.objects.filter(jid_id="inv1").count() == 1
  invalidate_job_plot_cache_keys_for_jids(["inv1"])
  assert job_plot_artifact.objects.filter(jid_id="inv1").count() == 0


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
