"""Phase 1: RabbitMQ -> listend_drain -> sync_timedb once -> update_metrics."""
from __future__ import annotations

import os
import sys
from datetime import timedelta

import pika
import pytest
from datetime import timezone as dt_timezone
from django.utils import timezone as django_timezone

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.analysis.metrics.lib.metrics import (
    expected_job_metric_row_count,
    job_metrics_catalog_entries,
)
from hpcperfstats.analysis.metrics.update_metrics import main as update_metrics_main
from hpcperfstats.dbload.sync_timedb import run_ingest_entire_archive_once_for_tests
from hpcperfstats.listend_drain import drain_queue_to_archive
from hpcperfstats.site.lib.machine.models import ApiKey, host_data, job_data, job_plot_artifact, metrics_data

from .constants import (
    PIPELINE_E2E_API_RAW_KEY,
    PIPELINE_E2E_HOST_SHORT,
    PIPELINE_E2E_JID,
    PIPELINE_E2E_USERNAME,
)
from .monitor_payloads import (
    PMU_SHARING_E2E_OUTSIDE_PCT,
    PMU_SHARING_E2E_RUNNING_PCT,
    pipeline_e2e_publish_bodies_multihost,
)


@pytest.mark.django_db(transaction=True)
def test_full_rabbitmq_ingest_metrics_pipeline():
  if os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip() != "1":
    pytest.skip("Requires compose network (db, rabbitmq, valkey); run workflow script.")
  if os.environ.get("HPCPERFSTATS_PIPELINE_E2E", "").strip().lower() not in (
      "1", "yes", "true"):
    pytest.skip("Pipeline E2E gate is disabled (set HPCPERFSTATS_PIPELINE_E2E=1).")

  host_ext = cfg.get_host_name_ext().strip().lstrip(".")
  fqdn = "{}.{}".format(PIPELINE_E2E_HOST_SHORT, host_ext)
  fqdn2 = "{}.{}".format(PIPELINE_E2E_HOST_SHORT + "b", host_ext)

  kh = ApiKey.hash_raw_key(PIPELINE_E2E_API_RAW_KEY)
  ApiKey.objects.filter(key=kh).delete()
  ApiKey.objects.create(
      key=kh,
      key_prefix=PIPELINE_E2E_API_RAW_KEY[:12],
      username=PIPELINE_E2E_USERNAME,
      is_staff=True,
  )

  # Keep this test restart-safe/idempotent across repeated workflow runs.
  metrics_data.objects.filter(jid_id=PIPELINE_E2E_JID).delete()
  job_plot_artifact.objects.filter(jid_id=PIPELINE_E2E_JID).delete()
  host_data.objects.filter(host__in=[fqdn, fqdn2]).delete()
  job_data.objects.filter(jid=PIPELINE_E2E_JID).delete()
  now = django_timezone.now()
  if now.tzinfo is None:
    now = now.replace(tzinfo=dt_timezone.utc)
  end_job = now - timedelta(days=3)
  start_job = end_job - timedelta(hours=2)
  margin = timedelta(minutes=10)
  job_data.objects.create(
      jid=PIPELINE_E2E_JID,
      submit_time=start_job,
      start_time=start_job,
      end_time=end_job,
      username=PIPELINE_E2E_USERNAME,
      host_list=[PIPELINE_E2E_HOST_SHORT, PIPELINE_E2E_HOST_SHORT + "b"],
      state="COMPLETED",
      runtime=7200.0,
      nhosts=2,
      ncores=4,
  )

  epoch_samples = [
      start_job.timestamp(),
      (start_job + timedelta(minutes=40)).timestamp(),
      (start_job + timedelta(minutes=95)).timestamp(),
      (start_job + timedelta(minutes=150)).timestamp(),
      (end_job - timedelta(minutes=5)).timestamp(),
      (end_job + margin).timestamp(),
  ]

  bodies = pipeline_e2e_publish_bodies_multihost(
      fqdns=[fqdn, fqdn2],
      jid=PIPELINE_E2E_JID,
      epoch_samples=epoch_samples,
  )

  os.makedirs(cfg.get_daily_archive_dir_path(), exist_ok=True)
  queue_name = cfg.get_rmq_queue()
  parameters = pika.ConnectionParameters(cfg.get_rmq_server())
  connection = pika.BlockingConnection(parameters)
  channel = connection.channel()
  channel.queue_declare(queue=queue_name, durable=True)
  for text in bodies:
    channel.basic_publish(
        exchange="",
        routing_key=queue_name,
        body=text.encode("utf-8"),
        properties=pika.BasicProperties(delivery_mode=2),
    )
  connection.close()

  drained = drain_queue_to_archive()
  assert drained == len(bodies)

  # In-process ingest so workers use pytest-django's test DB (a subprocess would
  # follow [DEFAULT] dbname from ini and write to the non-test database).
  run_ingest_entire_archive_once_for_tests()

  local_end = django_timezone.localtime(end_job)
  day = local_end.date().isoformat()
  update_metrics_main(
      [sys.argv[0], day, day],
      sleep_after=False,
  )

  assert host_data.objects.filter(host=fqdn).exists()
  assert host_data.objects.filter(host=fqdn2).exists()
  assert host_data.objects.filter(type="amd_x86_pmc").exists()
  assert host_data.objects.filter(type="amd_x86_uncore_df").exists()
  assert host_data.objects.filter(type="arm_aarch64_imc").exists()
  assert host_data.objects.filter(type="amd_gpu").exists()
  assert host_data.objects.filter(
      type="nvidia_gpu", event="gpu_mem_util",
  ).exists()
  assert host_data.objects.filter(
      type="nvidia_gpu", event="gpu_util",
  ).exists()
  # Two-tier ingest: fast samples omit slow-tier events; full samples include them.
  assert not host_data.objects.filter(
      type="nvidia_gpu", event="gpu_mem_used_mb",
  ).filter(value__isnull=True).exists()
  assert job_plot_artifact.objects.filter(jid_id=PIPELINE_E2E_JID).count() >= 1

  catalog_metrics = {e["metric"] for e in job_metrics_catalog_entries()}
  rows = list(metrics_data.objects.filter(jid_id=PIPELINE_E2E_JID))
  assert len(rows) == expected_job_metric_row_count()
  assert {r.metric for r in rows} == catalog_metrics
  half_baked = [
      r for r in rows
      if r.value is None
      and (r.no_data_reason is None or str(r.no_data_reason).strip() == "")
  ]
  assert not half_baked, "metrics_data rows need value or no_data_reason: %r" % (
      [(x.metric, x.value, x.no_data_reason) for x in half_baked],
  )
  # Contract 10 reasons: a type the job's schema lacks says "collector not
  # found" (with schema_absent codes); every code-backed reason names a type.
  schema = job_data.objects.get(pk=PIPELINE_E2E_JID).host_data_schema_json or {}
  for r in rows:
    codes = r.no_data_codes or {}
    if codes.get("schema_absent"):
      typ = str(r.no_data_reason).split(" ", 1)[0]
      assert r.value is None and typ not in schema, (r.metric, r.no_data_reason)
      assert r.no_data_reason == typ + " collector not found on this node's monitor"
    elif codes.get("codes") and r.value is None:
      assert ": " in str(r.no_data_reason) and "hosts)" in str(r.no_data_reason)
  # Grace PMU sharing (monitor 3.1 Phase 1b, D6): ratios of summed host_cpu_hw rates.
  by_metric = {r.metric: r.value for r in rows}
  for metric, want in (
      ("avg_pmu_running_pct", PMU_SHARING_E2E_RUNNING_PCT),
      ("avg_pmu_outside_pct", PMU_SHARING_E2E_OUTSIDE_PCT),
  ):
    got = by_metric.get(metric)
    assert got is not None and abs(float(got) - want) < 0.5, (
        "%s: expected ~%.1f from synthetic PMU counters, got %r"
        % (metric, want, got)
    )
  numeric = sum(1 for r in rows if r.value is not None)
  # NFS detail + a few ratio/imbalance metrics may stay ``None`` with reasons.
  assert numeric >= 35, (
      "Expected most catalog metrics numeric from rich synthetic telemetry; "
      "got %d / %d. Nulls: %s"
      % (
          numeric,
          len(rows),
          sorted(r.metric for r in rows if r.value is None),
      )
  )


@pytest.mark.django_db(transaction=True)
def test_sentinel_values_ingest_end_to_end():
  """Monitor value sentinels through RabbitMQ -> listend -> sync_timedb.

  A bridgeable code (-3 READ_FAILED) on one IB sample keeps the code in
  ``value`` with NULL delta/arc and the next sample's delta spans the gap; an
  all-device chain-breaking code (-2) on host_cpu is stored once as the code;
  ``host_sentinel_summary`` gets the runs.
  """
  if os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip() != "1":
    pytest.skip("Requires compose network (db, rabbitmq, valkey); run workflow script.")
  if os.environ.get("HPCPERFSTATS_PIPELINE_E2E", "").strip().lower() not in (
      "1", "yes", "true"):
    pytest.skip("Pipeline E2E gate is disabled (set HPCPERFSTATS_PIPELINE_E2E=1).")
  from hpcperfstats.site.lib.machine.models import host_sentinel_summary

  host_ext = cfg.get_host_name_ext().strip().lstrip(".")
  fqdn = "{}s.{}".format(PIPELINE_E2E_HOST_SHORT, host_ext)
  host_data.objects.filter(host=fqdn).delete()
  host_sentinel_summary.objects.filter(host=fqdn).delete()
  header = (
      "$hpcperfstats 3.1\n$hostname {h}\n$uname Linux x86_64 5.14.0 test\n"
      "$uptime 1\n$build x86_64-ver3.1-hw-ib-likwid-full\n"
      "!host_ib port_xmit_data,E port_rcv_data,E\n"
      "!host_cpu user,E,U=cs idle,E,U=cs\n"
  ).format(h=fqdn)
  base = (django_timezone.now() - timedelta(days=3)).timestamp()
  ib = ["1000 2000", "-3 -3", "1600 2600", "1700 2700"]
  cpu = [("10 90", "11 91"), ("-2 -2", "-2 -2"), ("30 70", "31 71"), ("40 60", "41 61")]
  bodies = [header]
  for i, (ib_v, (c0, c1)) in enumerate(zip(ib, cpu, strict=True)):
    bodies.append(
        "%.6f sjob %s\nhost_ib mlx5_0.1 %s\nhost_cpu 0 %s\nhost_cpu 1 %s\n"
        % (base + 60 * i, fqdn, ib_v, c0, c1)
    )
  bodies.append(header)

  os.makedirs(cfg.get_daily_archive_dir_path(), exist_ok=True)
  queue_name = cfg.get_rmq_queue()
  connection = pika.BlockingConnection(pika.ConnectionParameters(cfg.get_rmq_server()))
  channel = connection.channel()
  channel.queue_declare(queue=queue_name, durable=True)
  for text in bodies:
    channel.basic_publish(
        exchange="",
        routing_key=queue_name,
        body=text.encode("utf-8"),
        properties=pika.BasicProperties(delivery_mode=2),
    )
  connection.close()
  assert drain_queue_to_archive() == len(bodies)
  run_ingest_entire_archive_once_for_tests()

  ib_rows = {
      round(r.time.timestamp() - base): r
      for r in host_data.objects.filter(host=fqdn, type="host_ib", event="port_xmit_data")
  }
  assert ib_rows[60].value == -3
  assert ib_rows[60].delta is None and ib_rows[60].arc is None
  assert ib_rows[120].delta == 600  # bridged: 1600 - 1000 over the gap
  assert ib_rows[120].arc == pytest.approx(600 / 120)
  cpu_rows = {
      round(r.time.timestamp() - base): r
      for r in host_data.objects.filter(host=fqdn, type="host_cpu", event="user")
  }
  assert cpu_rows[60].value == -2
  assert cpu_rows[120].delta is None  # -2 breaks the chain
  assert cpu_rows[180].delta == 20
  assert not host_data.objects.filter(host=fqdn, value__lt=0, delta__isnull=False).exists()
  runs = {
      (r.type, r.event, r.code, r.device_count, r.total_devices)
      for r in host_sentinel_summary.objects.filter(host=fqdn)
  }
  assert ("host_ib", "port_xmit_data", -3, 1, 1) in runs
  assert ("host_cpu", "user", -2, 2, 2) in runs
