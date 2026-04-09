"""Phase 1: RabbitMQ → listend_drain → sync_timedb once → update_metrics."""
from __future__ import annotations

import os
import sys
from datetime import timedelta

import pika
import pytest
from datetime import timezone as dt_timezone
from django.utils import timezone as django_timezone

import hpcperfstats.conf_parser as cfg
from hpcperfstats.analysis.metrics.metrics import (
    expected_job_metric_row_count,
    job_metrics_catalog_entries,
)
from hpcperfstats.analysis.metrics.update_metrics import main as update_metrics_main
from hpcperfstats.dbload.sync_timedb import run_ingest_entire_archive_once_for_tests
from hpcperfstats.listend_drain import drain_queue_to_archive
from hpcperfstats.site.machine.models import ApiKey, host_data, job_data, job_plot_artifact, metrics_data

from .constants import (
    PIPELINE_E2E_API_RAW_KEY,
    PIPELINE_E2E_HOST_SHORT,
    PIPELINE_E2E_JID,
    PIPELINE_E2E_USERNAME,
)
from .monitor_payloads import pipeline_e2e_publish_bodies_multihost


@pytest.mark.django_db(transaction=True)
def test_full_rabbitmq_ingest_metrics_pipeline():
  if os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip() != "1":
    pytest.skip("Requires compose network (db, rabbitmq, redis); run workflow script.")
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
  start_job = now - timedelta(hours=4)
  end_job = now - timedelta(hours=2)
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
      (start_job + timedelta(minutes=12)).timestamp(),
      (start_job + timedelta(minutes=40)).timestamp(),
      (start_job + timedelta(minutes=95)).timestamp(),
      (start_job + timedelta(minutes=150)).timestamp(),
      (end_job - timedelta(minutes=40)).timestamp(),
      (end_job + margin).timestamp(),
  ]

  bodies = pipeline_e2e_publish_bodies_multihost(
      fqdns=[fqdn, fqdn2],
      jid=PIPELINE_E2E_JID,
      epoch_samples=epoch_samples,
  )

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
  # follow PORTAL.dbname from ini and write to the non-test database).
  run_ingest_entire_archive_once_for_tests()

  local_end = django_timezone.localtime(end_job)
  day = local_end.date().isoformat()
  update_metrics_main(
      [sys.argv[0], day, day],
      sleep_after=False,
  )

  assert host_data.objects.filter(host=fqdn).exists()
  assert host_data.objects.filter(host=fqdn2).exists()
  assert host_data.objects.filter(type="amd64_pmc").exists()
  assert host_data.objects.filter(type="amd64_df").exists()
  assert host_data.objects.filter(type="arm_imc").exists()
  assert host_data.objects.filter(type="amd_gpu").exists()
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
