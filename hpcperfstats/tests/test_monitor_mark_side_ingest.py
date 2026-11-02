"""Shared-node %jobs / %affinity parse, dev stamp, and exclusive regression."""

from pathlib import Path

from hpcperfstats.dbload.lib.monitor_mark_side_ingest import (
  collect_marks,
  cpu_list_from_token,
  detect_shared_node_contract,
  gpu_list_from_token,
  job_dev_tokens,
  parse_affinity_mark,
  parse_jobs_jids,
  persist_monitor_marks,
  shared_dev_for_row,
)
from hpcperfstats.dbload.lib.sync_timedb_parsing import (
  IncrementalStatsParser,
  build_stats_dataframes,
  compute_deltas_and_arc,
  parse_stats_lines,
)


def test_parse_jobs_and_affinity_tokens():
  assert parse_jobs_jids("%jobs jids=12,34") == ["12", "34"]
  assert parse_jobs_jids("%begin 12") == []
  assert parse_jobs_jids("%jobs jids=") == []
  mark = parse_affinity_mark("%affinity jid=9 cpus=0-3,8 gpus=- cg=sched")
  assert mark["jid"] == "9"
  assert cpu_list_from_token(mark["cpus"]) == ["0-3", "8"]
  assert gpu_list_from_token(mark["gpus"]) == []
  assert detect_shared_node_contract(["%jobs jids=9"]) is True
  assert detect_shared_node_contract("1710000001.0 9 h\ncpu 0 1\n") is False
  assert shared_dev_for_row("cpu", "0", "42", True) == "j:42"
  assert shared_dev_for_row("nvidia_gpu", "0", "42", True) == "0"
  assert shared_dev_for_row("cpu", "0", "42", False) == "0"
  assert job_dev_tokens(["42", "7"]) == ["j:42", "j:7"]


def test_collect_marks_binds_following_header():
  rows = collect_marks(
    [
      "%jobs jids=7,8",
      "%affinity jid=7 cpus=0-1 gpus=0 cg=sched",
      "%begin 7",
      "1710000001.0 7 hosta",
      "cpu 0 1",
    ]
  )
  kinds = [row["kind"] for row in rows]
  assert kinds == ["jobs", "affinity"]
  assert rows[0]["jids"] == ["7", "8"]
  assert rows[0]["host"] == "hosta"
  assert rows[0]["time"] == 1710000001.0
  assert rows[1]["jid"] == "7"


def _collapsed_devs(lines):
  stats_list, proc_list = parse_stats_lines(lines, 0)
  stats_df, _proc_df = build_stats_dataframes(stats_list, proc_list)
  collapsed = compute_deltas_and_arc(stats_df)
  return [
    (str(row.type), str(row.dev), str(row.jid))
    for row in collapsed.itertuples(index=False)
  ]


def test_shared_samples_stamp_cpu_dev_and_keep_gpu_dev():
  lines = [
    "!cpu user sys\n",
    "!nvidia_gpu util\n",
    "%jobs jids=123,456\n",
    "%affinity jid=123 cpus=0-1 gpus=0 cg=sched\n",
    "1710000001.0 123 hosta\n",
    "cpu 0 10 1\n",
    "cpu 1 20 2\n",
    "nvidia_gpu 0 5\n",
    "%jobs jids=123,456\n",
    "%affinity jid=456 cpus=4-7 gpus=- cg=sched\n",
    "1710000001.0 456 hosta\n",
    "cpu 0 30 3\n",
    "nvidia_gpu 1 7\n",
  ]
  rows = _collapsed_devs(lines)
  cpu = sorted(item for item in rows if item[0] == "cpu")
  gpu = sorted(item for item in rows if item[0] == "nvidia_gpu")
  assert ("cpu", "j:123", "123") in cpu
  assert ("cpu", "j:456", "456") in cpu
  assert ("nvidia_gpu", "0", "123") in gpu
  assert ("nvidia_gpu", "1", "456") in gpu
  assert all(not item[1].startswith("j:") for item in gpu)


def test_exclusive_sample_keeps_empty_dev():
  lines = [
    "!cpu user sys\n",
    "1710000001.0 job1 hosta\n",
    "cpu 0 10 1\n",
    "cpu 1 20 2\n",
  ]
  rows = _collapsed_devs(lines)
  assert rows
  assert {item[1] for item in rows} == {""}
  assert {item[2] for item in rows} == {"job1"}


def test_unknown_percent_lines_do_not_arm_shared_stamp():
  parser = IncrementalStatsParser(0)
  parser.feed_lines(
    [
      "!cpu user sys\n",
      "%begin 9\n",
      "%end 9\n",
      "1710000001.0 job1 hosta\n",
      "cpu 0 10 1\n",
    ]
  )
  assert parser.marks == []
  assert parser._sample_shared is False


def test_multi_header_amqp_body_binds_marks_per_block():
  """One AMQP body may merge several sample blocks; marks bind per header."""
  lines = [
    "!cpu user sys\n",
    "%jobs jids=111,222\n",
    "%affinity jid=111 cpus=0-1 gpus=0 cg=sched\n",
    "1710000001.0 111 hosta\n",
    "cpu 0 10 1\n",
    "%jobs jids=111,222\n",
    "%affinity jid=222 cpus=2-3 gpus=1 cg=sched\n",
    "1710000001.0 222 hosta\n",
    "cpu 0 20 2\n",
    "1710000001.0 - hosta\n",
    "cpu 0 30 3\n",
  ]
  parser = IncrementalStatsParser(0)
  parser.feed_lines(lines)
  ready = parser.take_ready_marks()
  jobs = [m for m in ready if m["kind"] == "jobs"]
  aff = [m for m in ready if m["kind"] == "affinity"]
  assert len(jobs) == 2
  assert {tuple(m["jids"]) for m in jobs} == {("111", "222")}
  assert {m["jid"] for m in aff} == {"111", "222"}
  assert all(m["host"] == "hosta" for m in ready)
  assert all(m["time"] == 1710000001.0 for m in ready)
  rows = _collapsed_devs(lines)
  cpu = sorted(item for item in rows if item[0] == "cpu")
  assert ("cpu", "j:111", "111") in cpu
  assert ("cpu", "j:222", "222") in cpu
  assert any(item[1] == "" for item in cpu)
  assert ("cpu", "j:-", "-") not in cpu
  assert not any(
    item[1].startswith("j:") and item[2] in ("-", "") for item in cpu
  )


def test_sole_full_resource_sample_without_jobs_keeps_empty_dev():
  """Sole full CPU+GPU grant emits neither mark; looks like exclusive path."""
  lines = [
    "!cpu user sys\n",
    "1710000001.0 999 hosta\n",
    "cpu 0 10 1\n",
  ]
  assert detect_shared_node_contract(lines) is False
  rows = _collapsed_devs(lines)
  assert rows
  assert {item[1] for item in rows} == {""}
  assert {item[2] for item in rows} == {"999"}


def test_membership_is_host_data_dev_not_a_second_hypertable():
  import inspect

  from hpcperfstats.site.lib.machine import models

  source = inspect.getsource(persist_monitor_marks)
  assert "host_sample_jid" not in source
  assert "job_affinity" in source
  assert not hasattr(models, "host_sample_jid")
  migration = Path(
    "hpcperfstats/site/lib/machine/migrations/0036_job_affinity.py"
  ).read_text()
  assert "job_affinity" in migration
  assert "create_hypertable" not in migration
  assert "host_sample_jid" not in migration


def test_jobs_mark_without_affinity_writes_no_affinity_row(monkeypatch):
  """horizon-idle-allocation-tagging: an idle (title-named) job on a
  multi-job node is in ``%jobs`` but never gets an ``%affinity`` line. Its
  non-GPU rows still take the ``j:<jid>`` stamp, GPU rows keep hardware
  ``dev``, and no ``job_affinity`` row is written (so the job stays
  unrestricted on GPUs instead of getting an empty grant)."""
  lines = [
    "!host_mem_job mem_used\n",
    "!nvidia_gpu util\n",
    "%jobs jids=111,79249\n",
    "%affinity jid=111 cpus=0-3 gpus=0 cg=sched\n",
    "1710000001.0 111 hosta\n",
    "nvidia_gpu 0 5\n",
    "host_mem_job j:111 10\n",
    "%jobs jids=111,79249\n",
    "1710000001.0 79249 hosta\n",
    "host_mem_job j:79249 20\n",
    "nvidia_gpu 1 7\n",
  ]
  rows = _collapsed_devs(lines)
  assert ("host_mem_job", "j:79249", "79249") in rows
  assert ("nvidia_gpu", "1", "79249") in rows
  marks = collect_marks(lines)
  assert [m["kind"] for m in marks] == ["jobs", "affinity", "jobs"]
  assert [m["jid"] for m in marks if m["kind"] == "affinity"] == ["111"]

  written = []

  class _Objects:
    @staticmethod
    def update_or_create(jid, host, defaults):
      written.append((jid, host, defaults["gpu_list"]))

  import hpcperfstats.site.lib.machine.models as models

  monkeypatch.setattr(models.job_affinity, "objects", _Objects)
  persist_monitor_marks(marks)
  assert written == [("111", "hosta", ["0"])]
