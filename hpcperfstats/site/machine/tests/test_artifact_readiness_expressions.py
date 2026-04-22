"""Regression: SQL artifact fingerprints match Python helpers (PostgreSQL)."""

import pytest
from django.db import connection
from django.utils import timezone

from hpcperfstats.conf_parser import get_host_name_ext
from hpcperfstats.site.machine.artifact_readiness_expressions import (
    DetailArtifactInputFingerprintHex,
    PlotArtifactInputFingerprintHex,
)
from hpcperfstats.site.machine.job_detail_artifacts import compute_detail_input_fingerprint
from hpcperfstats.site.machine.job_plot_artifacts import (
    compute_plot_input_fingerprint,
    get_live_distinct_time_count_for_jid,
)
from hpcperfstats.site.machine.models import job_data


@pytest.mark.django_db
def test_plot_sql_fingerprint_matches_python_helper():
  if connection.vendor != "postgresql":
    pytest.skip("PostgreSQL-only SQL fingerprint")
  now = timezone.now()
  j = job_data.objects.create(
      jid="sqlfp_plot1",
      submit_time=now,
      start_time=now,
      end_time=now,
      username="u1",
      host_list=["b.example.com", "a.example.com"],
      metrics_distinct_time_count=7,
      host_data_schema_json={"t1": {}},
  )
  live = get_live_distinct_time_count_for_jid(j.jid)
  py_fp = compute_plot_input_fingerprint(j, live)
  suffix = "." + get_host_name_ext()
  sql_fp = (
      job_data.objects.filter(pk=j.pk)
      .annotate(fp=PlotArtifactInputFingerprintHex(suffix))
      .values_list("fp", flat=True)
      .get()
  )
  assert sql_fp == py_fp


@pytest.mark.django_db
def test_detail_sql_fingerprint_matches_python_helper():
  if connection.vendor != "postgresql":
    pytest.skip("PostgreSQL-only SQL fingerprint")
  now = timezone.now()
  j = job_data.objects.create(
      jid="sqlfp_detail1",
      submit_time=now,
      start_time=now,
      end_time=now,
      username="u1",
      host_list=["h1.example.com"],
      metrics_distinct_time_count=None,
      host_data_schema_json={},
  )
  py_fp = compute_detail_input_fingerprint(j)
  sql_fp = (
      job_data.objects.filter(pk=j.pk)
      .annotate(fp=DetailArtifactInputFingerprintHex())
      .values_list("fp", flat=True)
      .get()
  )
  assert sql_fp == py_fp
