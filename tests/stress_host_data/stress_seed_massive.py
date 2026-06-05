"""Bulk INSERT for stress jobs: parametric hosts × time steps × metric pairs.

**Legacy row target:** ``HPCPERFSTATS_STRESS_HOST_DATA_ROWS`` (multiple of 40 × N hosts)
with ``HPCPERFSTATS_STRESS_INTERVAL_SEC`` default **1** (1 Hz).

**Time-rectangle mode:** set ``HPCPERFSTATS_STRESS_USE_TIME_SCALE=1`` and use
``HPCPERFSTATS_STRESS_N_HOSTS``, ``HPCPERFSTATS_STRESS_INTERVAL_SEC`` (e.g. 30),
``HPCPERFSTATS_STRESS_DURATION_SEC`` to size the grid; row count is
``n_hosts × n_steps × len(metric_pairs)``.

Post-``end_time`` **readiness probes** (one row per host) are inserted separately so
``update_metrics`` readiness (``latest(time) > end_time``) passes without inflating
the main window row count.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from django.db import connection
from django.utils import timezone as django_tz

from hpcperfstats.analysis.gen.utils import (
    INTEL_FP_ARITH_ALL_EVENTS,
    INTEL_FP_ARITH_DOUBLE_EVENTS,
    INTEL_FP_ARITH_SINGLE_EVENTS,
)
from hpcperfstats.site.machine.models import host_data, job_data


def stress_jid() -> str:
  return (
      os.environ.get("HPCPERFSTATS_STRESS_JID", "stress_um_pipeline").strip()
      or "stress_um_pipeline"
  )


def stress_short_hostname(i: int = 1) -> str:
  return "stressh{:06d}".format(i)


def metric_pairs():
  """(type, event) rows; total length must divide target row count per host-step."""
  pairs = [
      ("cpu_counter_metrics", "APERF"),
      ("cpu_counter_metrics", "INST_RETIRED"),
  ]
  for ev in INTEL_FP_ARITH_ALL_EVENTS:
    pairs.append(("intel_8pmc3", ev))
  pairs += [
      ("intel_skx_imc", "CAS_READS"),
      ("intel_skx_imc", "CAS_WRITES"),
      ("net", "rx_bytes"),
      ("net", "tx_bytes"),
      ("amd64_pmc", "APERF"),
      ("amd64_pmc", "INST_RETIRED"),
      ("opa", "PortXmitData"),
      ("opa", "PortRcvData"),
      ("nfs", "normal_read"),
      ("nfs", "normal_write"),
      ("host_ib", "port_xmit_data"),
      ("host_ib", "port_rcv_data"),
      ("host_ib", "port_xmit_pkts"),
      ("host_ib", "port_rcv_pkts"),
      ("nvidia_gpu", "gpu_util"),
      ("nvidia_gpu", "utilization"),
  ]
  _i4 = list(INTEL_FP_ARITH_DOUBLE_EVENTS) + list(INTEL_FP_ARITH_SINGLE_EVENTS)
  for ev in _i4:
    pairs.append(("intel_4pmc3", ev))
  pairs += [
      ("intel_hsw_imc", "CAS_READS"),
      ("intel_hsw_imc", "CAS_WRITES"),
      ("intel_bdw_imc", "CAS_READS"),
      ("intel_bdw_imc", "CAS_WRITES"),
      ("cpu_counter_metrics", "MPERF"),
      ("amd_gpu", "tensor_active"),
  ]
  assert len(pairs) == 40, len(pairs)
  return pairs


def target_row_count() -> int:
  env = os.environ.get("HPCPERFSTATS_STRESS_HOST_DATA_ROWS", "").strip()
  if env:
    return max(40, int(env) // 40 * 40)
  return 34_560_000


def _truthy_env(name: str) -> bool:
  return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class StressSeedDimensions:
  n_hosts: int
  n_steps: int
  interval_sec: int
  n_metrics: int
  n_rows: int
  use_time_scale: bool


def stress_seed_dimensions() -> StressSeedDimensions:
  """Resolved host/time grid and total ``host_data`` row count (before probes)."""
  pairs = metric_pairs()
  n_metrics = len(pairs)
  n_hosts = max(1, int(os.environ.get("HPCPERFSTATS_STRESS_N_HOSTS", "1")))
  use_time = _truthy_env("HPCPERFSTATS_STRESS_USE_TIME_SCALE")

  if use_time:
    interval_sec = max(1, int(os.environ.get("HPCPERFSTATS_STRESS_INTERVAL_SEC", "30")))
    duration_sec = max(
        interval_sec * 2,
        int(os.environ.get("HPCPERFSTATS_STRESS_DURATION_SEC", "1800")),
    )
    n_steps = max(2, duration_sec // interval_sec)
    n_rows = n_hosts * n_steps * n_metrics
    return StressSeedDimensions(
        n_hosts=n_hosts,
        n_steps=n_steps,
        interval_sec=interval_sec,
        n_metrics=n_metrics,
        n_rows=n_rows,
        use_time_scale=True,
    )

  n_rows = target_row_count()
  interval_sec = max(1, int(os.environ.get("HPCPERFSTATS_STRESS_INTERVAL_SEC", "1")))
  prod = n_metrics * n_hosts
  if n_rows % prod != 0:
    raise ValueError(
        "HPCPERFSTATS_STRESS_HOST_DATA_ROWS-derived count {} not divisible by "
        "{} metrics × {} hosts".format(n_rows, n_metrics, n_hosts)
    )
  n_steps = n_rows // prod
  return StressSeedDimensions(
      n_hosts=n_hosts,
      n_steps=n_steps,
      interval_sec=interval_sec,
      n_metrics=n_metrics,
      n_rows=n_rows,
      use_time_scale=False,
  )


def delete_stress_job(jid: str) -> None:
  host_data.objects.filter(jid=jid).delete()
  job_data.objects.filter(jid=jid).delete()


def _utc_now():
  t0 = django_tz.now()
  if t0.tzinfo is None:
    from datetime import timezone as py_utc

    t0 = t0.replace(tzinfo=py_utc.utc)
  return t0


def insert_readiness_probes(
    *,
    jid: str,
    host_ext: str,
    probe_time,
    n_hosts: int,
) -> int:
  """One synthetic row per host strictly after ``job.end_time`` (readiness)."""
  ext = host_ext.strip().lstrip(".")
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
       ('stressh' || lpad(h.n::text, 6, '0') || '.' || %s)::text,
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
    cursor.execute(sql, [probe_time, ext, jid, n_hosts])
  return n_hosts


def insert_stress_job_and_host_data(
    *,
    host_ext: str,
    username: str = "stressuser",
    post_end_probe_seconds: int | None = None,
) -> tuple[str, str, int, int]:
  """Create ``job_data`` + bulk ``host_data`` + readiness probes.

  Returns ``(jid, sample_fqdn, n_rows, live_distinct_expected)`` where
  ``live_distinct_expected`` = ``n_hosts * n_steps`` (jid + window live distinct sum).
  """
  jid = stress_jid()
  dims = stress_seed_dimensions()
  pairs = metric_pairs()
  ext = host_ext.strip().lstrip(".")
  delete_stress_job(jid)

  t0 = _utc_now()
  delta = timedelta(seconds=dims.interval_sec)
  t_last = t0 + delta * (dims.n_steps - 1)
  margin = timedelta(seconds=int(os.environ.get(
      "HPCPERFSTATS_STRESS_JOB_END_MARGIN_SEC", "3600")))
  end_time = t_last + margin
  probe_delta = timedelta(seconds=int(
      post_end_probe_seconds
      if post_end_probe_seconds is not None
      else os.environ.get("HPCPERFSTATS_STRESS_PROBE_AFTER_END_SEC", "120")
  ))
  probe_time = end_time + probe_delta

  runtime_sec = max(300.0, float(dims.n_steps * dims.interval_sec))
  host_list = [stress_short_hostname(i) for i in range(1, dims.n_hosts + 1)]

  job_data.objects.create(
      jid=jid,
      submit_time=t0,
      start_time=t0,
      end_time=end_time,
      username=username,
      host_list=host_list,
      state="COMPLETED",
      runtime=runtime_sec,
      nhosts=dims.n_hosts,
      ncores=1,
  )

  values_sql_parts = []
  params: list = []
  for typ, ev in pairs:
    values_sql_parts.append("(%s::text, %s::text)")
    params.extend([typ, ev])
  values_clause = ", ".join(values_sql_parts)

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

  interval_literal = "{} seconds".format(dims.interval_sec)

  sql = """
INSERT INTO {tbl} ({col_time}, {col_host}, {col_jid}, {col_type}, {col_dev}, {col_event},
                   {col_unit}, {col_value}, {col_arc}, {col_delta})
SELECT ts,
       ('stressh' || lpad(h.n::text, 6, '0') || '.' || %s)::text,
       %s::text,
       v.metric_type,
       NULL::varchar,
       v.metric_event,
       'count'::varchar,
       (EXTRACT(EPOCH FROM ts)::double precision),
       (1000.0 + random() * 1e6)::double precision,
       NULL::double precision
FROM generate_series(%s::timestamptz, %s::timestamptz, %s::interval) AS ts
CROSS JOIN generate_series(1, %s) AS h(n)
CROSS JOIN (VALUES {vals}) AS v(metric_type, metric_event)
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
      vals=values_clause,
  )

  args: list = [ext, jid, t0, t_last, interval_literal, dims.n_hosts] + params
  with connection.cursor() as cursor:
    cursor.execute(sql, args)

  insert_readiness_probes(
      jid=jid,
      host_ext=host_ext,
      probe_time=probe_time,
      n_hosts=dims.n_hosts,
  )

  sample_fqdn = "{}.{}".format(stress_short_hostname(1), ext)
  live_expected = dims.n_hosts * dims.n_steps
  return jid, sample_fqdn, dims.n_rows, live_expected


def stress_host_fqdns(host_ext: str, n_hosts: int) -> list[str]:
  ext = host_ext.strip().lstrip(".")
  return ["{}.{}".format(stress_short_hostname(i), ext) for i in range(1, n_hosts + 1)]
