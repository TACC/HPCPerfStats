#!/usr/bin/env python3
"""Microbenchmark of listend's raw-archive append path.

Times ``append_monitor_payload_to_archive`` on synthetic monitor payloads
(one ``\\n<ts> <jid> <host>`` header per sample, ``%jobs`` mark, alpha
rows) across round-robin hosts, the way one host-affine archive thread
sees traffic. Every message carries fresh header timestamps unless the
case is a re-send, so the timings cover the normal append path. Prints
median ns/msg and MB/s per case.

Attributes:
  CASES: ``(name, payload_bytes, samples_per_payload, shared_ts, resend)``
    rows timed by ``main``.
"""

from __future__ import annotations

import argparse
import os
import queue
import shutil
import statistics
import sys
import time

CASES: tuple[tuple[str, int, int, bool, bool], ...] = (
  ("4KiB_x1", 4 << 10, 1, False, False),
  ("64KiB_x1", 64 << 10, 1, False, False),
  ("1MiB_x1", 1 << 20, 1, False, False),
  ("1MiB_x10", 1 << 20, 10, False, False),
  ("48KiB_burst3", 48 << 10, 3, True, False),
  ("64KiB_x1_resend", 64 << 10, 1, False, True),
)


def _rows(nbytes: int) -> bytes:
  """
  Return alpha-leading stats rows totalling about ``nbytes`` bytes.

  Args:
    nbytes (int): Target body size in bytes.

  Returns:
    bytes: Newline-terminated ``cpu <dev> <values>`` rows.

  Examples:
    >>> _rows(64).startswith(b"cpu 0 ")
    True
  """
  out: list[bytes] = []
  total = 0
  dev = 0
  while total < nbytes:
    vals = b" ".join(
      b"%d" % (123456789 * (dev + k) % 10**12) for k in range(10)
    )
    row = b"cpu %d %s\n" % (dev, vals)
    out.append(row)
    total += len(row)
    dev += 1
  return b"".join(out)


def _payloads(
  hosts: list[str],
  n_msgs: int,
  nbytes: int,
  samples: int,
  shared_ts: bool,
  base_ts: float,
) -> list[bytes]:
  """
  Build ``n_msgs`` payloads with distinct header lines per host.

  Args:
    hosts (list[str]): Round-robin host FQDNs.
    n_msgs (int): Number of payloads to build.
    nbytes (int): Approximate payload size in bytes.
    samples (int): Samples per payload.
    shared_ts (bool): When True, the samples of one payload share one
      timestamp with different jids (shared-node burst).
    base_ts (float): First header timestamp.

  Returns:
    list[bytes]: Payloads in send order.

  Examples:
    >>> p = _payloads(["h.x"], 2, 256, 2, False, 1.7e9)
    >>> len(p), p[0].count(b" h.x\\n")
    (2, 2)
  """
  body = b"%jobs jids=100258\n" + _rows(max(64, nbytes // samples))
  out: list[bytes] = []
  for i in range(n_msgs):
    host = hosts[i % len(hosts)].encode()
    parts = []
    for j in range(samples):
      ts = base_ts + 60.0 * i + (0.0 if shared_ts else 0.25 * j)
      jid = b"%d" % (100258 + j) if shared_ts else b"100258"
      parts.append(b"\n%.6f %s %s\n%s" % (ts, jid, host, body))
    out.append(b"".join(parts))
  return out


def _time_case(ld: object, msgs: list[bytes], archive_dir: str) -> float:
  """
  Append every payload once and return elapsed nanoseconds.

  Args:
    ld (object): Imported ``hpcperfstats.listend`` module.
    msgs (list[bytes]): Payloads to append in order.
    archive_dir (str): Scratch archive root (emptied before the run).

  Returns:
    float: Wall nanoseconds for all appends.

  Examples:
    >>> callable(_time_case)
    True
  """
  ld._recent_host_queue = queue.Queue(maxsize=100000)
  append = ld.append_monitor_payload_to_archive
  t0 = time.perf_counter_ns()
  for m in msgs:
    append(m)
  return float(time.perf_counter_ns() - t0)


def main() -> int:
  """
  Run every case ``--repeats`` times and print median ns/msg and MB/s.

  Returns:
    int: Process exit code (0).

  Examples:
    >>> # ../.venv/bin/python3 scripts/bench_listend_archive_append.py
    >>> callable(main)
    True
  """
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument("--archive-dir", default="test_runs/listend_dedupe/bench")
  ap.add_argument("--hosts", type=int, default=64)
  ap.add_argument("--target-mib", type=int, default=128)
  ap.add_argument("--repeats", type=int, default=7)
  args = ap.parse_args()

  import hpcperfstats.listend as ld

  archive_dir = os.path.abspath(args.archive_dir)
  ld.cfg.get_archive_dir_path = lambda: archive_dir
  hosts = [f"c{i:03d}-001.bench.example.edu" for i in range(args.hosts)]
  print(f"python={sys.version.split()[0]} hosts={args.hosts} dir={archive_dir}")
  for case_i, (name, nbytes, samples, shared_ts, resend) in enumerate(CASES):
    n_msgs = max(args.hosts, (args.target_mib << 20) // nbytes)
    runs: list[float] = []
    msgs: list[bytes] = []
    for rep in range(args.repeats):
      shutil.rmtree(archive_dir, ignore_errors=True)
      ld._release_sticky_archive_writer()
      base = 1_795_000_000.0 + 1e7 * case_i + 1e5 * (0 if resend else rep)
      msgs = _payloads(hosts, n_msgs, nbytes, samples, shared_ts, base)
      if resend:
        for m in msgs:
          ld.append_monitor_payload_to_archive(m)
      runs.append(_time_case(ld, msgs, archive_dir))
    ld._release_sticky_archive_writer()
    total_bytes = sum(len(m) for m in msgs)
    med = statistics.median(runs)
    print(
      f"{name:18s} msgs={n_msgs:6d} bytes/msg={total_bytes // n_msgs:8d} "
      f"ns/msg={med / n_msgs:10.0f} MB/s={total_bytes / (med / 1e9) / 1e6:8.1f} "
      f"min_ns/msg={min(runs) / n_msgs:10.0f}"
    )
  shutil.rmtree(archive_dir, ignore_errors=True)
  return 0


if __name__ == "__main__":
  sys.exit(main())
