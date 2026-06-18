"""Compose-only seed helpers for update_metrics diagnosis tests.

Scale axis (documented): **approximate in-window host_data row count** per job
(one ``(cpu_counter_metrics, APERF)`` sample per host per timestep). Post-end
readiness probes are extra rows strictly after ``end_time`` (not counted).
"""
from __future__ import annotations

import os
from datetime import timedelta

from django.db import connection
from django.utils import timezone as django_tz

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.site.lib.machine.models import host_data, job_data


def _utc_now():
  t0 = django_tz.now()
  if t0.tzinfo is None:
    from datetime import timezone as py_utc

    t0 = t0.replace(tzinfo=py_utc.utc)
  return t0


def _short_host(prefix: str, i: int) -> str:
  # Short hostnames for job_data.host_list (FQDN built with host_name_ext).
  return "{}{:05d}".format(prefix, i)


def _delete_diagnosis_jobs(jids: list[str]) -> None:
  for jid in jids:
    host_data.objects.filter(jid=jid).delete()
    job_data.objects.filter(jid=jid).delete()


def _insert_window_rows(
    *,
    jid: str,
    host_prefix: str,
    n_hosts: int,
    n_steps: int,
    interval_sec: int,
    fqdn_ext: str,
    t0,
    t_last,
) -> int:
  """Bulk INSERT in-window rows; returns row count inserted."""
  ext = fqdn_ext.strip().lstrip(".")
  ops = connection.ops
  tbl = ops.quote_name(host_data._meta.db_table)
  col_time = ops.quote_name("time")
  col_host = ops.quote_name("host")
  col_jid = ops.quote_name("jid")
  col_type = ops.quote_name("type")
  col_dev = ops.quote_name("dev")
  col_event = ops.quote_name("event")
  col_unit = ops.quote_name("unit")
  col_value = ops.quote_name("value")
  col_arc = ops.quote_name("arc")
  col_delta = ops.quote_name("delta")
  interval_literal = "{} seconds".format(max(1, int(interval_sec)))
  sql = """
INSERT INTO {tbl} ({col_time}, {col_host}, {col_jid}, {col_type}, {col_dev}, {col_event},
                   {col_unit}, {col_value}, {col_arc}, {col_delta})
SELECT ts,
       (%s::text || lpad(h.n::text, 5, '0') || '.' || %s)::text,
       %s::text,
       'cpu_counter_metrics'::varchar,
       NULL::varchar,
       'APERF'::varchar,
       'count'::varchar,
       (EXTRACT(EPOCH FROM ts)::double precision),
       1000.0::double precision,
       NULL::double precision
FROM generate_series(%s::timestamptz, %s::timestamptz, %s::interval) AS ts
CROSS JOIN generate_series(1, %s) AS h(n)
""".format(
      tbl=tbl,
      col_time=col_time,
      col_host=col_host,
      col_jid=col_jid,
      col_type=col_type,
      col_dev=col_dev,
      col_event=col_event,
      col_unit=col_unit,
      col_value=col_value,
      col_arc=col_arc,
      col_delta=col_delta,
  )
  args = [host_prefix, ext, jid, t0, t_last, interval_literal, n_hosts]
  with connection.cursor() as cursor:
    cursor.execute(sql, args)
  return n_hosts * n_steps


def _insert_readiness_probes(
    *,
    jid: str,
    host_prefix: str,
    fqdn_ext: str,
    probe_time,
    n_hosts: int,
) -> int:
  ext = fqdn_ext.strip().lstrip(".")
  ops = connection.ops
  tbl = ops.quote_name(host_data._meta.db_table)
  col_time = ops.quote_name("time")
  col_host = ops.quote_name("host")
  col_jid = ops.quote_name("jid")
  col_type = ops.quote_name("type")
  col_dev = ops.quote_name("dev")
  col_event = ops.quote_name("event")
  col_unit = ops.quote_name("unit")
  col_value = ops.quote_name("value")
  col_arc = ops.quote_name("arc")
  col_delta = ops.quote_name("delta")
  sql = """
INSERT INTO {tbl} ({col_time}, {col_host}, {col_jid}, {col_type}, {col_dev}, {col_event},
                   {col_unit}, {col_value}, {col_arc}, {col_delta})
SELECT %s::timestamptz,
       (%s::text || lpad(h.n::text, 5, '0') || '.' || %s)::text,
       %s::text,
       'cpu_counter_metrics'::varchar,
       NULL::varchar,
       'APERF'::varchar,
       'count'::varchar,
       1.0::double precision,
       1000.0::double precision,
       NULL::double precision
FROM generate_series(1, %s) AS h(n)
""".format(
      tbl=tbl,
      col_time=col_time,
      col_host=col_host,
      col_jid=col_jid,
      col_type=col_type,
      col_dev=col_dev,
      col_event=col_event,
      col_unit=col_unit,
      col_value=col_value,
      col_arc=col_arc,
      col_delta=col_delta,
  )
  with connection.cursor() as cursor:
    cursor.execute(sql, [probe_time, host_prefix, ext, jid, n_hosts])
  return n_hosts


def seed_update_metrics_diagnosis_jobs():
  """Create two jobs: small cohort (100–300 in-window rows) and large (300–5000).

  Returns dict with jids, row counts, and local end date for ``update_metrics_for_dates``.
  """
  fqdn_ext = cfg.get_host_name_ext().strip().lstrip(".")

  small_hosts = max(1, int(os.environ.get("HPCPERFSTATS_UM_DIAG_SMALL_HOSTS", "10")))
  small_steps = max(1, int(os.environ.get("HPCPERFSTATS_UM_DIAG_SMALL_STEPS", "15")))
  large_hosts = max(1, int(os.environ.get("HPCPERFSTATS_UM_DIAG_LARGE_HOSTS", "25")))
  large_steps = max(1, int(os.environ.get("HPCPERFSTATS_UM_DIAG_LARGE_STEPS", "32")))

  jid_small = os.environ.get("HPCPERFSTATS_UM_DIAG_JID_SMALL", "um_diag_s1").strip()[:32]
  jid_large = os.environ.get("HPCPERFSTATS_UM_DIAG_JID_LARGE", "um_diag_l1").strip()[:32]
  margin_sec = int(os.environ.get("HPCPERFSTATS_UM_DIAG_END_MARGIN_SEC", "300"))
  probe_after_sec = int(os.environ.get("HPCPERFSTATS_UM_DIAG_PROBE_AFTER_END_SEC", "120"))
  interval_sec = int(os.environ.get("HPCPERFSTATS_UM_DIAG_INTERVAL_SEC", "60"))

  _delete_diagnosis_jobs([jid_small, jid_large])

  t0 = _utc_now()
  # Small job window
  n_rows_small = small_hosts * small_steps
  if not (100 <= n_rows_small <= 300):
    # Keep defaults inside band; allow env override outside for experiments.
    pass
  delta = timedelta(seconds=interval_sec)
  t_last_s = t0 + delta * (small_steps - 1)
  end_s = t_last_s + timedelta(seconds=margin_sec)
  probe_s = end_s + timedelta(seconds=probe_after_sec)
  host_list_s = [_short_host("ums", i) for i in range(1, small_hosts + 1)]
  runtime_s = max(300.0, float(small_steps * interval_sec))
  job_data.objects.create(
      jid=jid_small,
      submit_time=t0,
      start_time=t0,
      end_time=end_s,
      username="um_diag",
      host_list=host_list_s,
      state="COMPLETED",
      runtime=runtime_s,
      nhosts=small_hosts,
      ncores=1,
  )
  inserted_s = _insert_window_rows(
      jid=jid_small,
      host_prefix="ums",
      n_hosts=small_hosts,
      n_steps=small_steps,
      interval_sec=interval_sec,
      fqdn_ext=fqdn_ext,
      t0=t0,
      t_last=t_last_s,
  )
  _insert_readiness_probes(
      jid=jid_small,
      host_prefix="ums",
      fqdn_ext=fqdn_ext,
      probe_time=probe_s,
      n_hosts=small_hosts,
  )

  # Large job: same calendar window, disjoint host prefix so (time,host,type,event) stays unique.
  n_rows_large = large_hosts * large_steps
  t_last_l = t0 + delta * (large_steps - 1)
  end_l = t_last_l + timedelta(seconds=margin_sec)
  probe_l = end_l + timedelta(seconds=probe_after_sec)
  host_list_l = [_short_host("uml", i) for i in range(1, large_hosts + 1)]
  job_data.objects.create(
      jid=jid_large,
      submit_time=t0,
      start_time=t0,
      end_time=end_l,
      username="um_diag",
      host_list=host_list_l,
      state="COMPLETED",
      runtime=max(300.0, float(large_steps * interval_sec)),
      nhosts=large_hosts,
      ncores=1,
  )
  inserted_l = _insert_window_rows(
      jid=jid_large,
      host_prefix="uml",
      n_hosts=large_hosts,
      n_steps=large_steps,
      interval_sec=interval_sec,
      fqdn_ext=fqdn_ext,
      t0=t0,
      t_last=t_last_l,
  )
  _insert_readiness_probes(
      jid=jid_large,
      host_prefix="uml",
      fqdn_ext=fqdn_ext,
      probe_time=probe_l,
      n_hosts=large_hosts,
  )

  # update_metrics filters end_time__date; use latest end date in local TZ
  from django.utils import timezone

  latest_end = max(end_s, end_l)
  metrics_date = timezone.localtime(latest_end).replace(
      hour=0, minute=0, second=0, microsecond=0
  )

  return {
      "jid_small": jid_small,
      "jid_large": jid_large,
      "n_rows_small": inserted_s,
      "n_rows_large": inserted_l,
      "metrics_date": metrics_date,
      "fqdn_ext": fqdn_ext,
  }
