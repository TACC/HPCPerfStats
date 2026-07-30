"""Unit tests for staff_artifact_contract_payload (DB distinct schemas)."""
from __future__ import annotations

from django.utils import timezone
import pytest

from hpcperfstats.site.lib.machine import job_detail_artifacts as detail_cfg
from hpcperfstats.site.lib.machine import job_plot_artifacts as plot_cfg
from hpcperfstats.site.lib.machine.job_detail_artifacts import ARTIFACT_KIND_JOB_DETAIL
from hpcperfstats.site.lib.machine.models import (
    job_data,
    job_detail_artifact,
    job_plot_artifact,
)
from hpcperfstats.site.lib.machine.staff_artifact_contract import (
    staff_artifact_contract_payload,
)


@pytest.mark.django_db
def test_staff_artifact_contract_payload_empty_when_no_rows():
  now = timezone.now()
  job_data.objects.create(
      jid="sac-empty",
      submit_time=now,
      start_time=now,
      end_time=now,
      username="u1",
      host_list=["n1"],
      metrics_distinct_time_count=1,
  )
  out = staff_artifact_contract_payload("sac-empty")
  assert out["current_plot"] == plot_cfg.APP_PLOT_ARTIFACT_SCHEMA_VERSION
  assert out["current_detail"] == detail_cfg.APP_DETAIL_ARTIFACT_SCHEMA_VERSION
  assert out["db_plot"] == []
  assert out["db_detail"] == []
  assert "does not mean plots are missing" in (out.get("note") or "")


@pytest.mark.django_db
def test_staff_artifact_contract_payload_distinct_sorted_omits_null():
  now = timezone.now()
  job_data.objects.create(
      jid="sac-mixed",
      submit_time=now,
      start_time=now,
      end_time=now,
      username="u1",
      host_list=["n1"],
      metrics_distinct_time_count=1,
  )
  job_plot_artifact.objects.create(
      jid_id="sac-mixed",
      plot_kind="summary_plot",
      layout="normal",
      input_fingerprint="a",
      payload_compressed=b"x",
      payload_encoding="raw",
      artifact_schema=10,
  )
  job_plot_artifact.objects.create(
      jid_id="sac-mixed",
      plot_kind="roofline",
      layout="normal",
      input_fingerprint="b",
      payload_compressed=b"y",
      payload_encoding="raw",
      artifact_schema=11,
  )
  job_plot_artifact.objects.create(
      jid_id="sac-mixed",
      plot_kind="gpu_roofline",
      layout="normal",
      input_fingerprint="c",
      payload_compressed=b"z",
      payload_encoding="raw",
      artifact_schema=None,
  )
  job_detail_artifact.objects.create(
      jid_id="sac-mixed",
      artifact_kind=ARTIFACT_KIND_JOB_DETAIL,
      artifact_scope="",
      input_fingerprint="d",
      payload_compressed=b"w",
      payload_encoding="raw",
      artifact_schema=8,
  )
  out = staff_artifact_contract_payload("sac-mixed")
  assert out["db_plot"] == [10, 11]
  assert out["db_detail"] == [8]
