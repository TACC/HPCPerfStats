"""Targeted tests for job_detail artifact prewarm reuse paths."""

from datetime import timedelta

import pandas as pd
import pytest
from django.utils import timezone

from hpcperfstats.site.lib.machine import job_detail_artifacts as jda
from hpcperfstats.site.lib.machine.models import job_data, job_detail_artifact, metrics_data


def test_job_detail_artifacts_has_jid_table_import():
  """Guard against NameError in prewarm path when jid_table import is dropped."""
  assert hasattr(jda, "jid_table")
  assert hasattr(jda.jid_table, "jid_table")
  assert hasattr(jda, "plots")
  assert hasattr(jda.plots, "DevPlot")


def _mk_job(jid="detailtest1"):
  now = timezone.now()
  return job_data.objects.create(
      jid=jid[:32],
      submit_time=now - timedelta(minutes=2),
      start_time=now - timedelta(minutes=1),
      end_time=now,
      runtime=60.0,
      username="u1",
      host_list=["n1"],
  )


@pytest.mark.django_db
def test_persist_job_detail_null_fsio_metrics_allows_host_fallback(monkeypatch):
  """Catalog FSIO keys with all-null values must not lock out host_data fallback."""
  job = _mk_job("detail-no-data-reuse")
  for metric_name in (
      "detail_fsio_llite_read_mb",
      "detail_fsio_llite_write_mb",
      "detail_fsio_nfs_read_mb",
      "detail_fsio_nfs_write_mb",
  ):
    metrics_data.objects.create(
        jid=job,
        type="llite" if "llite" in metric_name else "nfs",
        metric=metric_name,
        units="MB",
        value=None,
        no_data_reason="no data",
    )
  for metric_name, value in (
      ("detail_gpu_active", 0.0),
      ("detail_gpu_util_max", 0.0),
      ("detail_gpu_util_mean", 0.0),
      ("detail_gpu_count", 0.0),
  ):
    metrics_data.objects.create(
        jid=job,
        type="gpu",
        metric=metric_name,
        units="count",
        value=value,
        no_data_reason=None,
    )

  class _FakeJt:
    acct_host_list = ["n1"]
    schema = {}
    start_time = job.start_time
    end_time = job.end_time

    def get_llite_delta_by_event(self):
      return pd.DataFrame(
          [
              {"event": "read_bytes", "delta_sum": 1048576.0},
              {"event": "write_bytes", "delta_sum": 0.0},
          ]
      )

    def get_nfs_delta_totals_mb(self):
      return None

    def get_aggregate_df(self, *args, **kwargs):
      del args, kwargs
      return pd.DataFrame(columns=["host", "time", "sum_val"])

  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_detail_artifacts.jid_table.jid_table",
      lambda _jid: _FakeJt(),
  )

  telemetry = {}
  jda.persist_job_detail_artifacts_for_jid(job.jid, context={"_telemetry": telemetry})
  assert telemetry.get("detail_fsio_fallback_queries", 0) >= 1
  assert telemetry.get("detail_fsio_metrics_reused", 0) == 0


@pytest.mark.django_db
def test_persist_job_detail_skips_type_detail_when_artifact_is_fresh(monkeypatch):
  job = _mk_job("detail-fresh-type-skip")
  fp = jda.compute_detail_input_fingerprint(job)
  job_detail_artifact.objects.create(
      jid=job,
      artifact_kind=jda.ARTIFACT_KIND_TYPE_DETAIL,
      artifact_scope="cpu",
      payload_compressed=b"abc",
      payload_encoding=jda.PAYLOAD_ENCODING_GZIP_JSON,
      input_fingerprint=fp,
  )

  class _FakeJt:
    acct_host_list = ["n1"]
    schema = {"cpu": ["user"]}
    start_time = job.start_time
    end_time = job.end_time

    def get_llite_delta_by_event(self):
      return pd.DataFrame()

    def get_nfs_delta_totals_mb(self):
      return None

  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_detail_artifacts.jid_table.jid_table",
      lambda _jid: _FakeJt(),
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_detail_artifacts._gpu_detail_from_jid_table",
      lambda _jt: {"gpu_active": None, "gpu_utilization_max": None, "gpu_utilization_mean": None, "gpu_count": None},
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_detail_artifacts.jid_table.TypeDetailDataProvider",
      lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("type detail generation should be skipped")),
  )

  jda.persist_job_detail_artifacts_for_jid(job.jid)


@pytest.mark.django_db
def test_persist_job_detail_records_type_detail_failure_as_fresh_unavailable(monkeypatch):
  job = _mk_job("detail-type-failure")

  class _FakeJt:
    acct_host_list = ["n1"]
    schema = {"cpu": ["user"]}
    start_time = job.start_time
    end_time = job.end_time

    def get_llite_delta_by_event(self):
      return pd.DataFrame()

    def get_nfs_delta_totals_mb(self):
      return None

  class _FailingDevPlot:
    def __init__(self, _provider, _hosts):
      pass

    def plot(self):
      raise RuntimeError("type detail blew up")

  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_detail_artifacts.jid_table.jid_table",
      lambda _jid: _FakeJt(),
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_detail_artifacts._gpu_detail_from_jid_table",
      lambda _jt: {"gpu_active": None, "gpu_utilization_max": None, "gpu_utilization_mean": None, "gpu_count": None},
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_detail_artifacts.jid_table.TypeDetailDataProvider",
      lambda *args, **kwargs: object(),
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_detail_artifacts.plots.DevPlot",
      _FailingDevPlot,
  )

  jda.persist_job_detail_artifacts_for_jid(job.jid)

  fp = jda.compute_detail_input_fingerprint(job)
  payload = jda.load_job_detail_artifact(
      job.jid,
      jda.ARTIFACT_KIND_TYPE_DETAIL,
      "cpu",
      fp,
  )
  assert payload is not None
  assert payload["tplot_item"] is None
  assert payload["stats_data"] == []
  assert payload["schema"] == []
  assert payload["tplot_unavailable_reason"] == (
      "Type detail plot generation failed during artifact prewarm."
  )


@pytest.mark.django_db
def test_persist_job_detail_prewarms_multiprecision_mix_payload(monkeypatch):
  job = _mk_job("detail-multiprecision-mix")
  for metric_name, metric_type, value, units in (
      ("avg_flops64b", "pmc", 40.0, "GF"),
      ("avg_flops32b", "pmc", 60.0, "GF"),
      ("avg_tensor_active", "nvidia_gpu", 25.0, "%"),
      ("avg_fp16_active", "nvidia_gpu", 25.0, "%"),
      ("avg_fp32_active", "nvidia_gpu", 30.0, "%"),
      ("avg_fp64_active", "nvidia_gpu", 20.0, "%"),
  ):
    metrics_data.objects.create(
        jid=job,
        type=metric_type,
        metric=metric_name,
        units=units,
        value=value,
        no_data_reason=None,
    )

  class _FakeJt:
    acct_host_list = ["n1"]
    schema = {}
    start_time = job.start_time
    end_time = job.end_time

    def get_llite_delta_by_event(self):
      return pd.DataFrame()

    def get_nfs_delta_totals_mb(self):
      return None

  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_detail_artifacts.jid_table.jid_table",
      lambda _jid: _FakeJt(),
  )

  jda.persist_job_detail_artifacts_for_jid(job.jid)
  fp = jda.compute_detail_input_fingerprint(job)
  payload = jda.load_job_detail_artifact(
      job.jid,
      jda.ARTIFACT_KIND_MULTIPRECISION_MIX,
      "",
      fp,
  )
  assert payload is not None
  assert payload["cpu_plot_item"] is not None
  assert payload["gpu_plot_item"] is not None
  assert payload["cpu_unavailable_reason"] is None
  assert payload["gpu_unavailable_reason"] is None


@pytest.mark.django_db
def test_persist_job_detail_multiprecision_gpu_uses_available_widths_only(monkeypatch):
  """GPU pie should render with whichever precision widths are present for the job."""
  job = _mk_job("detail-multiprecision-dynamic-widths")
  for metric_name, metric_type, value in (
      ("avg_flops64b", "pmc", 55.0),
      ("avg_flops32b", "pmc", 45.0),
      ("avg_tensor_active", "nvidia_gpu", 70.0),
      ("avg_fp16_active", "nvidia_gpu", 30.0),
  ):
    metrics_data.objects.create(
        jid=job,
        type=metric_type,
        metric=metric_name,
        units="%",
        value=value,
        no_data_reason=None,
    )

  class _FakeJt:
    acct_host_list = ["n1"]
    schema = {}
    start_time = job.start_time
    end_time = job.end_time

    def get_llite_delta_by_event(self):
      return pd.DataFrame()

    def get_nfs_delta_totals_mb(self):
      return None

  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_detail_artifacts.jid_table.jid_table",
      lambda _jid: _FakeJt(),
  )

  jda.persist_job_detail_artifacts_for_jid(job.jid)
  fp = jda.compute_detail_input_fingerprint(job)
  payload = jda.load_job_detail_artifact(
      job.jid,
      jda.ARTIFACT_KIND_MULTIPRECISION_MIX,
      "",
      fp,
  )
  assert payload is not None
  assert payload["gpu_plot_item"] is not None
  assert payload["gpu_unavailable_reason"] is None


@pytest.mark.django_db
def test_persist_job_detail_multiprecision_gpu_unavailable_without_metrics(monkeypatch):
  """Without persisted GPU avg_*_active metrics, the GPU pie is unavailable
  (no host_data fallback)."""
  job = _mk_job("detail-multiprecision-no-gpu-metrics")
  for metric_name in (
      "avg_flops64b",
      "avg_flops32b",
  ):
    metrics_data.objects.create(
        jid=job,
        type="pmc",
        metric=metric_name,
        units="GF",
        value=50.0,
        no_data_reason=None,
    )

  class _FakeJt:
    acct_host_list = ["n1"]
    schema = {}
    start_time = job.start_time
    end_time = job.end_time

    def get_llite_delta_by_event(self):
      return pd.DataFrame()

    def get_nfs_delta_totals_mb(self):
      return None

  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_detail_artifacts.jid_table.jid_table",
      lambda _jid: _FakeJt(),
  )

  jda.persist_job_detail_artifacts_for_jid(job.jid)
  fp = jda.compute_detail_input_fingerprint(job)
  payload = jda.load_job_detail_artifact(
      job.jid,
      jda.ARTIFACT_KIND_MULTIPRECISION_MIX,
      "",
      fp,
  )
  assert payload is not None
  assert payload["cpu_plot_item"] is not None
  assert payload["gpu_plot_item"] is None
  assert payload["gpu_unavailable_reason"] == (
      "Missing GPU precision-width mix metrics in job metrics "
      "(need positive avg_*_active shares)."
  )
