"""Golden parity for IncrementalStatsParser.feed_line (E6 hot-path guard)."""
from __future__ import annotations

from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    IncrementalStatsParser,
    parse_stats_lines,
)


_E6_FIXTURE_LINES = [
    "1709123456 job1 cn001\n",
    "!cpu user,W=48 sys,W=48\n",
    "!host_proc vm_peak,U=kB rss_peak,U=kB\n",
    "cpu 0 @full 10 20\n",
    "host_proc bash/1/0/0 @full 100 50\n",
    "1709123457 job1 cn001\n",
    "cpu 0 @fast 11 21\n",
    "host_proc bash/1/0/0 @fast 110\n",
]


def _payload_fingerprint(stats_list, proc_list):
  stats = [
      (
          row.get("time"),
          row.get("host"),
          row.get("jid"),
          row.get("type"),
          row.get("event"),
          row.get("dev"),
          row.get("value"),
      )
      for row in stats_list
  ]
  procs = [
      (
          row.get("time"),
          row.get("host"),
          row.get("jid"),
          row.get("proc"),
          row.get("device"),
          row.get("vm_peak"),
          row.get("rss_peak"),
      )
      for row in proc_list
  ]
  return stats, procs


def test_e6_feed_line_fixture_parity_via_parse_stats_lines():
  """E6 feed_line compression must preserve hardware + host_proc emit parity."""
  stats_list, proc_list = parse_stats_lines(list(_E6_FIXTURE_LINES), 0)
  stats, procs = _payload_fingerprint(stats_list, proc_list)
  assert stats == [
      (1709123456.0, "cn001", "job1", "cpu", "user", "0", 10.0),
      (1709123456.0, "cn001", "job1", "cpu", "sys", "0", 20.0),
      (1709123457.0, "cn001", "job1", "cpu", "user", "0", 11.0),
      (1709123457.0, "cn001", "job1", "cpu", "sys", "0", 21.0),
  ]
  assert len(procs) == 1
  assert procs[0][:5] == (
      1709123457.0, "cn001", "job1", "bash", "bash/1/0/0",
  )
  assert procs[0][5] == 110


def test_e6_feed_lines_matches_line_loop():
  """Batch feed_lines must match per-line feed_line on the E6 fixture."""
  a = IncrementalStatsParser(0)
  for line in _E6_FIXTURE_LINES:
    a.feed_line(line)
  stats_a, procs_a = a.finish()

  b = IncrementalStatsParser(0)
  b.feed_lines(list(_E6_FIXTURE_LINES))
  stats_b, procs_b = b.finish()

  assert _payload_fingerprint(stats_a, procs_a) == _payload_fingerprint(
      stats_b, procs_b,
  )
