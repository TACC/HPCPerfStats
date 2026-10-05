"""Shared oracle helpers for db-offload Phase T compose tests."""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from hpcperfstats.dbload.lib.sync_timedb_parsing import (
  HOST_PROC_PEAK_KEYS,
  merge_proc_row_dicts,
  peak_merge_proc_objs_with_existing,
)
from hpcperfstats.site.lib.machine.models import host_data, job_data, proc_data


def compose_network_enabled() -> bool:
  return os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip().lower() in (
    "1",
    "yes",
    "true",
  )


def skip_unless_compose_db() -> None:
  import pytest

  if not compose_network_enabled():
    pytest.skip(
      "Requires Docker Compose network (PostgreSQL at host 'db'). "
      "Run: tests/run_db_pytest_workflow.sh"
    )


def proc_row_dict(**overrides: Any) -> dict[str, Any]:
  base: dict[str, Any] = {
    "jid": "900001",
    "host": "n001.test",
    "proc": "bash",
    "device": "bash/1",
    "uid": 1000,
    "vm_peak": 100,
    "vm_size": 100,
    "vm_lck": 0,
    "vm_hwm": 100,
    "vm_rss": 100,
    "vm_data": 100,
    "vm_stk": 100,
    "vm_exe": 100,
    "vm_lib": 100,
    "vm_pte": 100,
    "vm_swap": 0,
    "threads": 1,
  }
  base.update(overrides)
  return base


def proc_model(**overrides: Any) -> proc_data:
  return proc_data(**proc_row_dict(**overrides))


def peak_fields(row: proc_data) -> dict[str, Any]:
  return {k: getattr(row, k) for k in HOST_PROC_PEAK_KEYS}


def oracle_merge_peak_rows(
  existing: dict[str, Any],
  incoming: dict[str, Any],
) -> dict[str, Any]:
  return merge_proc_row_dicts(dict(existing), dict(incoming))


def oracle_sequential_second_presence(
  pairs: Iterable[tuple[str, int]],
) -> dict[tuple[str, int], bool]:
  """Reference: one exists() query per (host, unix_second)."""
  from hpcperfstats.dbload.lib import sync_timedb_host_itimes as hi

  hi.reset_host_itimes_caches()
  out: dict[tuple[str, int], bool] = {}
  for host, unix_second in pairs:
    key = (str(host).strip(), int(unix_second))
    out[key] = bool(hi.host_timestamp_second_present_in_db(key[0], key[1]))
  return out


def seed_minimal_job(
  *,
  jid: str,
  start,
  end,
  host_list: list[str],
) -> None:
  job_data.objects.create(
    jid=jid,
    submit_time=start,
    start_time=start,
    end_time=end,
    username="dboff",
    host_list=host_list,
    state="COMPLETED",
  )


def seed_host_data_second(host: str, unix_second: int) -> None:
  ts = datetime.fromtimestamp(int(unix_second), tz=UTC)
  host_data.objects.create(
    jid="900001",
    host=host,
    time=ts,
    type="cpu_counter_metrics",
    event="APERF",
    dev="",
    unit="count",
    value=1.0,
  )


def seed_host_data_seconds(host: str, unix_seconds: set[int]) -> None:
  for sec in unix_seconds:
    seed_host_data_second(host, sec)


def peak_merge_then_insert(
  objs: list[proc_data],
  *,
  insert_fn: Any,
  lookup_chunk_size: int = 256,
) -> None:
  peak_merge_proc_objs_with_existing(objs, lookup_chunk_size=lookup_chunk_size)
  insert_fn(objs)
