"""Shared dbload ORM row builders (bulk vs fallback use the same field mapping)."""
from __future__ import annotations

import pandas as pd

from hpcperfstats.dbload.lib.date_utils import to_pydatetime_or_none
from hpcperfstats.site.lib.machine.models import host_data, job_data


def _dev_str_from_stats_row(row) -> str:
  """Monitor device id for ``host_data.dev``; missing/NaN → ``''`` (not NULL)."""
  dev_val = getattr(row, "dev", None)
  if dev_val is None or (isinstance(dev_val, float) and pd.isna(dev_val)):
    return ""
  if pd.isna(dev_val):
    return ""
  s = str(dev_val).strip()
  if not s or s.lower() == "nan":
    return ""
  return s


def host_data_instance_from_stats_row(row) -> host_data:
  """Build an unsaved ``host_data`` from a stats DataFrame row (namedtuple)."""
  jid_val = getattr(row, "jid", None)
  if pd.notna(jid_val) and str(jid_val) != "-":
    jid_str = str(jid_val)
  else:
    jid_str = None
  return host_data(
      time=to_pydatetime_or_none(row.time),
      host=row.host,
      jid=jid_str,
      type=row.type,
      dev=_dev_str_from_stats_row(row),
      event=row.event,
      unit=row.unit,
      value=float(row.value) if pd.notna(row.value) else None,
      delta=float(row.delta) if pd.notna(row.delta) else None,
      arc=float(row.arc) if pd.notna(row.arc) else None,
  )


def job_data_instance_from_acct_row(row) -> job_data:
  """Build an unsaved ``job_data`` from an accounting DataFrame row (namedtuple)."""
  return job_data(
      jid=str(row.jid),
      username=row.username,
      account=row.account if pd.notna(row.account) else None,
      start_time=to_pydatetime_or_none(row.start_time),
      end_time=to_pydatetime_or_none(row.end_time),
      submit_time=to_pydatetime_or_none(row.submit_time),
      queue=row.queue if pd.notna(row.queue) else None,
      timelimit=float(row.timelimit) if pd.notna(row.timelimit) else None,
      jobname=str(row.jobname) if pd.notna(row.jobname) else None,
      state=row.state if pd.notna(row.state) else None,
      nhosts=int(row.nhosts) if pd.notna(row.nhosts) else None,
      ncores=int(row.ncores) if pd.notna(row.ncores) else None,
      host_list=list(row.host_list) if row.host_list else [],
      runtime=float(row.runtime) if pd.notna(row.runtime) else None,
      node_hrs=float(row.node_hrs) if pd.notna(row.node_hrs) else None,
  )
