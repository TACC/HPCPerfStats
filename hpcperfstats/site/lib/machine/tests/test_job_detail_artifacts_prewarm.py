"""Targeted tests for job_detail artifact prewarm reuse paths."""

from datetime import timedelta

import pytest
from django.utils import timezone

from hpcperfstats.site.lib.machine import job_detail_artifacts as jda
from hpcperfstats.site.lib.machine.models import (
  job_data,
  job_detail_artifact,
  metrics_data,
)


def test_job_detail_artifacts_has_job_table_imports():
  """Guard against NameError in the prewarm path when the table imports drop."""
  assert hasattr(jda, "open_job_table")
  assert hasattr(jda, "HANDOFF")
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


def _fake_table(job, *, schema=None, llite=None, nfs=None, beegfs=None):
  """Real (empty) columnar table for ``job`` with the FSIO reads stubbed.

  The detail prewarm calls every columnar accessor (GPU inventory, node
  power, type detail); an empty JobFrame answers them with "no data" while
  the stubbed Lustre / NFS / BeeGFS reads drive the FSIO fallback paths.
  """
  from hpcperfstats.analysis.metrics.lib.columnar.testing import (
    table_from_records,
  )

  table = table_from_records(
    [],
    jid=str(job.jid),
    hosts=["n1"],
    schema=dict(schema or {}),
    start_time=job.start_time,
    end_time=job.end_time,
  )
  table.get_llite_delta_by_event = lambda: dict(llite or {})
  table.get_nfs_delta_totals_mb = lambda: nfs
  table.get_beegfs_delta_by_event = lambda: dict(beegfs or {})
  return table


@pytest.mark.django_db(transaction=True)
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

  fake_jt = _fake_table(
    job,
    schema={},
    llite={"read_bytes": 1048576.0, "write_bytes": 0.0},
    nfs=None,
    beegfs={},
  )
  monkeypatch.setattr(
    "hpcperfstats.site.lib.machine.job_detail_artifacts.open_job_table",
    lambda _jid, **_k: fake_jt,
  )

  telemetry = {}
  jda.persist_job_detail_artifacts_for_jid(
    job.jid, context={"_telemetry": telemetry}
  )
  assert telemetry.get("detail_fsio_fallback_queries", 0) >= 1
  assert telemetry.get("detail_fsio_metrics_reused", 0) == 0


@pytest.mark.django_db(transaction=True)
def test_persist_job_detail_skips_type_detail_when_artifact_is_fresh(
  monkeypatch,
):
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

  fake_jt = _fake_table(
    job,
    schema={"cpu": ["user"]},
    llite={},
    nfs=None,
    beegfs={},
  )
  monkeypatch.setattr(
    "hpcperfstats.site.lib.machine.job_detail_artifacts.open_job_table",
    lambda _jid, **_k: fake_jt,
  )
  monkeypatch.setattr(
    "hpcperfstats.site.lib.machine.job_detail_artifacts._gpu_detail_from_jid_table",
    lambda _jt: {
      "gpu_active": None,
      "gpu_utilization_max": None,
      "gpu_utilization_mean": None,
      "gpu_count": None,
    },
  )
  monkeypatch.setattr(
    "hpcperfstats.site.lib.machine.job_detail_artifacts.build_type_detail_series",
    lambda *args, **kwargs: (_ for _ in ()).throw(
      AssertionError("type detail generation should be skipped")
    ),
  )

  jda.persist_job_detail_artifacts_for_jid(job.jid)


@pytest.mark.django_db(transaction=True)
def test_persist_job_detail_records_type_detail_failure_as_fresh_unavailable(
  monkeypatch,
):
  job = _mk_job("detail-type-failure")

  class _FakeJt:
    acct_host_list = ["n1"]
    schema = {"cpu": ["user"]}
    start_time = job.start_time
    end_time = job.end_time
    units = {}
    _shared_node = False

    def get_llite_delta_by_event(self):
      return {}

    def get_nfs_delta_totals_mb(self):
      return None

    def get_beegfs_delta_by_event(self):
      return {}

  class _FailingDevPlot:
    def __init__(self, _provider, _hosts):
      pass

    def plot(self):
      raise RuntimeError("type detail blew up")

  monkeypatch.setattr(
    "hpcperfstats.site.lib.machine.job_detail_artifacts.open_job_table",
    lambda _jid, **_k: _FakeJt(),
  )
  monkeypatch.setattr(
    "hpcperfstats.site.lib.machine.job_detail_artifacts._gpu_detail_from_jid_table",
    lambda _jt: {
      "gpu_active": None,
      "gpu_utilization_max": None,
      "gpu_utilization_mean": None,
      "gpu_count": None,
    },
  )
  monkeypatch.setattr(
    "hpcperfstats.site.lib.machine.job_detail_artifacts.build_type_detail_series",
    lambda *args, **kwargs: object(),
  )
  monkeypatch.setattr(
    "hpcperfstats.site.lib.machine.job_detail_artifacts.plots.DevPlot",
    _FailingDevPlot,
  )

  from hpcperfstats.site.lib.machine.job_plot_artifacts import (
    ARTIFACT_BUILD_RETRY_LIMIT,
    ArtifactBuildFailedError,
  )

  fp = jda.compute_detail_input_fingerprint(job)
  # Retryable type-detail failures are misses until the budget is spent.
  for _ in range(ARTIFACT_BUILD_RETRY_LIMIT - 1):
    with pytest.raises(ArtifactBuildFailedError):
      jda.persist_job_detail_artifacts_for_jid(job.jid)
    assert (
      jda.load_job_detail_artifact(
        job.jid, jda.ARTIFACT_KIND_TYPE_DETAIL, "cpu", fp
      )
      is None
    )
  jda.persist_job_detail_artifacts_for_jid(job.jid)
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


@pytest.mark.django_db(transaction=True)
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

  fake_jt = _fake_table(
    job,
    schema={},
    llite={},
    nfs=None,
    beegfs={},
  )
  monkeypatch.setattr(
    "hpcperfstats.site.lib.machine.job_detail_artifacts.open_job_table",
    lambda _jid, **_k: fake_jt,
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


@pytest.mark.django_db(transaction=True)
def test_persist_job_detail_multiprecision_gpu_uses_available_widths_only(
  monkeypatch,
):
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

  fake_jt = _fake_table(
    job,
    schema={},
    llite={},
    nfs=None,
    beegfs={},
  )
  monkeypatch.setattr(
    "hpcperfstats.site.lib.machine.job_detail_artifacts.open_job_table",
    lambda _jid, **_k: fake_jt,
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


@pytest.mark.django_db(transaction=True)
def test_persist_job_detail_multiprecision_gpu_unavailable_without_metrics(
  monkeypatch,
):
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

  fake_jt = _fake_table(
    job,
    schema={},
    llite={},
    nfs=None,
    beegfs={},
  )
  monkeypatch.setattr(
    "hpcperfstats.site.lib.machine.job_detail_artifacts.open_job_table",
    lambda _jid, **_k: fake_jt,
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


class _FailingFsioJt:
  """Job table stand-in whose Lustre fallback read fails (no DB)."""

  acct_host_list = ["n1"]
  schema = {}
  units = {}
  start_time = None
  end_time = None
  _shared_node = False

  def get_llite_delta_by_event(self):
    from django.db.utils import OperationalError

    raise OperationalError("canceling statement due to statement timeout")

  def get_nfs_delta_totals_mb(self):
    return None

  def get_beegfs_delta_by_event(self):
    return {}


def _patch_detail_without_db(monkeypatch, *, stored_detail_fp=None):
  """Stub every DB / host_data touch of the job-detail prewarm."""
  upserts = []
  monkeypatch.setattr(jda, "_metric_value_map", lambda job: {})
  monkeypatch.setattr(jda, "_fsio_from_metric_values", lambda *_a: ({}, False))
  monkeypatch.setattr(
    jda, "extend_fsio_payload_lists_with_peaks", lambda fsio, jt: None
  )
  monkeypatch.setattr(
    jda, "_gpu_detail_from_metric_values", lambda *_a: {"gpu_inventory": [1]}
  )
  monkeypatch.setattr(jda, "_multiprecision_mix_payload", lambda *a, **k: {})
  stored = {} if stored_detail_fp is None else stored_detail_fp
  if isinstance(stored, str):
    stored = {(jda.ARTIFACT_KIND_JOB_DETAIL, ""): stored}
  monkeypatch.setattr(jda, "_load_detail_fingerprints", lambda j: dict(stored))
  monkeypatch.setattr(
    jda, "upsert_job_detail_artifact", lambda **kw: upserts.append(kw)
  )
  return upserts


@pytest.mark.machine_unit_mock
def test_detail_fsio_failure_stores_retry_fingerprint_and_raises(monkeypatch):
  """S2: a failed FSIO sub-query must not be stored as current detail."""
  from types import SimpleNamespace

  from hpcperfstats.site.lib.machine.job_plot_artifacts import (
    ArtifactBuildFailedError,
  )

  upserts = _patch_detail_without_db(monkeypatch)
  fp = "d" * 64
  ctx = {
    "job": SimpleNamespace(jid="fsiofail1", host_data_schema_json={}),
    "jt": _FailingFsioJt(),
    "detail_fingerprint": fp,
  }
  with pytest.raises(ArtifactBuildFailedError) as ei:
    jda.persist_job_detail_artifacts_for_jid("fsiofail1", context=ctx)
  assert ei.value.parts == ["fsio"]
  by_kind = {u["artifact_kind"]: u["input_fingerprint"] for u in upserts}
  assert by_kind[jda.ARTIFACT_KIND_JOB_DETAIL].startswith("retry1:")
  assert by_kind[jda.ARTIFACT_KIND_JOB_DETAIL] != fp
  assert by_kind[jda.ARTIFACT_KIND_MULTIPRECISION_MIX] == fp


@pytest.mark.machine_unit_mock
def test_detail_fsio_failure_turns_terminal_after_retry_budget(monkeypatch):
  """S2: the last allowed failure stores the real fingerprint, no raise."""
  from types import SimpleNamespace

  fp = "d" * 64
  upserts = _patch_detail_without_db(
    monkeypatch, stored_detail_fp="retry2:" + fp[:57]
  )
  ctx = {
    "job": SimpleNamespace(jid="fsiofail2", host_data_schema_json={}),
    "jt": _FailingFsioJt(),
    "detail_fingerprint": fp,
  }
  jda.persist_job_detail_artifacts_for_jid("fsiofail2", context=ctx)
  by_kind = {u["artifact_kind"]: u["input_fingerprint"] for u in upserts}
  assert by_kind[jda.ARTIFACT_KIND_JOB_DETAIL] == fp


@pytest.mark.machine_unit_mock
def test_detail_and_mix_with_current_fingerprint_are_not_rebuilt(monkeypatch):
  """T6: current job_detail + mix rows skip the FSIO/GPU rebuild and upsert."""
  from types import SimpleNamespace

  fp = "d" * 64
  upserts = _patch_detail_without_db(
    monkeypatch,
    stored_detail_fp={
      (jda.ARTIFACT_KIND_JOB_DETAIL, ""): fp,
      (jda.ARTIFACT_KIND_MULTIPRECISION_MIX, ""): fp,
    },
  )
  ctx = {
    "job": SimpleNamespace(jid="fresh1", host_data_schema_json={}),
    "jt": _FailingFsioJt(),  # a rebuild would raise in the llite fallback
    "detail_fingerprint": fp,
  }
  jda.persist_job_detail_artifacts_for_jid("fresh1", context=ctx)
  assert upserts == []
