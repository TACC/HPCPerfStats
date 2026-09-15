#!/usr/bin/env python3
"""Wave 4 microbenchmark: online ownership merge vs append+copy dedupe.

Compares the merge strategy only (same row construction cost), not ASCII
feed_line parsing — that cost is identical before/after Wave 4.
"""
from __future__ import annotations

import time
from typing import Any

from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    HOST_PROC_PEAK_KEYS,
    dedupe_proc_stats_peak_merge,
    merge_proc_row_dicts,
)


def _make_row(i: int, p: int) -> dict[str, Any]:
  """
  Build one synthetic host_proc row for the merge microbenchmark.

  Args:
    i (int): Sample index (drives peak/thread values).
    p (int): Process index (drives ``proc`` name).

  Returns:
    dict[str, Any]: Sparse host_proc-like mapping.

  Examples:
    >>> _make_row(0, 1)["proc"]
    'proc1'
  """
  row: dict[str, Any] = {
      "time": float(1_700_000_000 + i),
      "host": "cn001",
      "jid": "job1",
      "proc": f"proc{p}",
      "device": f"proc{p}/{p}/0/0",
      "vm_peak": 1000 + (i % 50),
      "vm_hwm": 500 + (i % 50),
      "vm_stk": 10,
      "vm_exe": 1,
      "vm_lib": 2,
      "threads": 1 + (i % 8),
  }
  for k in HOST_PROC_PEAK_KEYS:
    row.setdefault(k, 0)
  return row


def legacy_append_copy_dedupe(
  n_samples: int,
  n_procs: int,
) -> list[dict[str, Any]]:
  """
  Pre-Wave-4: keep every sample, then first-hit ``dict(row)`` dedupe.

  Args:
    n_samples (int): Number of time samples.
    n_procs (int): Distinct ``(jid,host,proc)`` names per sample.

  Returns:
    list[dict[str, Any]]: One row per unique proc name.

  Examples:
    >>> len(legacy_append_copy_dedupe(2, 3))
    3
  """
  rows = [_make_row(i, p) for i in range(n_samples) for p in range(n_procs)]
  by_key: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
  for row in rows:
    key = (row.get("jid"), row.get("host"), row.get("proc"))
    if key in by_key:
      by_key[key] = merge_proc_row_dicts(by_key[key], row)
    else:
      by_key[key] = dict(row)
  return list(by_key.values())


def online_ownership_merge(
  n_samples: int,
  n_procs: int,
) -> list[dict[str, Any]]:
  """
  Wave 4: merge as rows are born; first hit takes ownership.

  Args:
    n_samples (int): Number of time samples.
    n_procs (int): Distinct ``(jid,host,proc)`` names per sample.

  Returns:
    list[dict[str, Any]]: One row per unique proc name.

  Examples:
    >>> len(online_ownership_merge(2, 3))
    3
  """
  by_key: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
  for i in range(n_samples):
    for p in range(n_procs):
      row = _make_row(i, p)
      key = (row.get("jid"), row.get("host"), row.get("proc"))
      existing = by_key.get(key)
      if existing is None:
        by_key[key] = row
      else:
        merge_proc_row_dicts(existing, row)
  return list(by_key.values())


def main() -> None:
  """
  Run the Wave 4 merge microbenchmark and print ``SPEEDUP_OK`` on success.

  Returns:
    None

  Raises:
    SystemExit: When the online path is not faster than the legacy copy path.

  Examples:
    >>> main()  # doctest: +SKIP
  """
  n_samples = 800
  n_procs = 128
  legacy_append_copy_dedupe(10, 8)
  online_ownership_merge(10, 8)
  t0 = time.perf_counter()
  legacy = legacy_append_copy_dedupe(n_samples, n_procs)
  legacy_s = time.perf_counter() - t0
  t1 = time.perf_counter()
  online = online_ownership_merge(n_samples, n_procs)
  online_s = time.perf_counter() - t1
  assert len(legacy) == n_procs
  assert len(online) == n_procs
  big = [_make_row(i, p) for i in range(n_samples) for p in range(n_procs)]
  t2 = time.perf_counter()
  out = dedupe_proc_stats_peak_merge(big)
  helper_s = time.perf_counter() - t2
  assert len(out) == n_procs
  speedup = legacy_s / online_s if online_s > 0 else float("inf")
  print(
      "legacy_copy_s=%.4f online_own_s=%.4f helper_own_s=%.4f "
      "speedup=%.2fx rows=%d unique=%d"
      % (
          legacy_s,
          online_s,
          helper_s,
          speedup,
          n_samples * n_procs,
          n_procs,
      ),
  )
  if online_s < legacy_s:
    print("SPEEDUP_OK")
  else:
    raise SystemExit(
        "SPEEDUP_FAIL online_own_s=%.4f legacy_copy_s=%.4f"
        % (online_s, legacy_s),
    )


if __name__ == "__main__":
  main()
