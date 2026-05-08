"""Targeted tests for job_detail artifact prewarm reuse paths."""

from datetime import timedelta

import pandas as pd
import pytest
from django.utils import timezone

from hpcperfstats.site.machine import job_detail_artifacts as jda
from hpcperfstats.site.machine.models import job_data, job_detail_artifact, metrics_data


def _mk_job(jid="detailtest1"):
  now = timezone.now()
  return job_data.objects.create(
      jid=jid,
      submit_time=now - timedelta(minutes=2),
      start_time=now - timedelta(minutes=1),
      end_time=now,
      runtime=60.0,
      username="u1",
      host_list=["n1"],
  )


@pytest.mark.django_db
def test_persist_job_detail_uses_metrics_no_data_without_fsio_fallback(monkeypatch):
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
      raise AssertionError("FSIO fallback should not run when metrics rows are present")

    def get_nfs_delta_totals_mb(self):
      raise AssertionError("NFS fallback should not run when metrics rows are present")

  monkeypatch.setattr(
      "hpcperfstats.site.machine.job_detail_artifacts.jid_table.jid_table",
      lambda _jid: _FakeJt(),
  )

  telemetry = {}
  jda.persist_job_detail_artifacts_for_jid(job.jid, context={"_telemetry": telemetry})
  assert telemetry.get("detail_fsio_fallback_queries", 0) == 0
  assert telemetry.get("detail_fsio_metrics_reused", 0) == 1


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
      "hpcperfstats.site.machine.job_detail_artifacts.jid_table.jid_table",
      lambda _jid: _FakeJt(),
  )
  monkeypatch.setattr(
      "hpcperfstats.site.machine.job_detail_artifacts._gpu_detail_from_jid_table",
      lambda _jt: {"gpu_active": None, "gpu_utilization_max": None, "gpu_utilization_mean": None, "gpu_count": None},
  )
  monkeypatch.setattr(
      "hpcperfstats.site.machine.job_detail_artifacts.jid_table.TypeDetailDataProvider",
      lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("type detail generation should be skipped")),
  )

  jda.persist_job_detail_artifacts_for_jid(job.jid)


@pytest.mark.django_db
def test_persist_job_detail_prewarms_multiprecision_mix_payload(monkeypatch):
  job = _mk_job("detail-multiprecision-mix")
  for metric_name, metric_type, value in (
      ("vecpercent_64b", "pmc", 40.0),
      ("vecpercent_32b", "pmc", 60.0),
      ("avg_tensor_active", "nvidia_gpu", 25.0),
      ("avg_fp16_active", "nvidia_gpu", 25.0),
      ("avg_fp32_active", "nvidia_gpu", 30.0),
      ("avg_fp64_active", "nvidia_gpu", 20.0),
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
      "hpcperfstats.site.machine.job_detail_artifacts.jid_table.jid_table",
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
def test_persist_job_detail_multiprecision_gpu_unavailable_without_metrics(monkeypatch):
  """Without persisted GPU avg_*_active metrics, the GPU pie is unavailable
  (no host_data fallback)."""
  job = _mk_job("detail-multiprecision-no-gpu-metrics")
  for metric_name in (
      "vecpercent_64b",
      "vecpercent_32b",
  ):
    metrics_data.objects.create(
        jid=job,
        type="pmc",
        metric=metric_name,
        units="%",
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
      "hpcperfstats.site.machine.job_detail_artifacts.jid_table.jid_table",
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
