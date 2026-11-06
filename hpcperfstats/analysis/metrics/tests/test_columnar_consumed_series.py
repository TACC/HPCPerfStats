"""Consumed-series recorder: stages, jid isolation, flush, FrameIndex guard."""

import ast
import inspect
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from hpcperfstats.analysis.metrics.lib.columnar import (
  consumed_series as cs,
  kernels as kn,
)
from hpcperfstats.analysis.metrics.lib.columnar.jobframe import JobFrame
from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
)
from hpcperfstats.analysis.metrics.lib.columnar.type_detail_series import (
  build_type_detail_series,
)

US = 1_000_000


@pytest.fixture(autouse=True)
def _clean_registry():
  cs._active.clear()
  cs._flush_error_logged = False
  yield
  cs._active.clear()


class _Pipe:
  def __init__(self, fail=False):
    self.hsets = []
    self.expires = []
    self.executed = 0
    self.fail = fail

  def hset(self, key, mapping):
    self.hsets.append((key, dict(mapping)))

  def expire(self, key, ttl):
    self.expires.append((key, ttl))

  def execute(self):
    if self.fail:
      raise ConnectionError("valkey down")
    self.executed += 1
    return []


class _Client:
  def __init__(self, fail=False):
    self.pipes = []
    self.fail = fail

  def pipeline(self, transaction=False):
    assert transaction is False
    p = _Pipe(self.fail)
    self.pipes.append(p)
    return p


def _frame(jid="j1"):
  return JobFrame.from_records(
    jid,
    [
      (1 * US, "h1", "host_cpu", "user", "0", 1, 1.0, 2.0, 1.0),
      (2 * US, "h1", "host_cpu", "user", "0", 1, 1.0, 3.0, 1.0),
      (1 * US, "h1", "host_cpu", "nice", "0", 1, 1.0, 2.0, 1.0),
      (1 * US, "h1", "nvidia_gpu", "gpu_util", "0", 1, 50.0, None, None),
      (1 * US, "h1", "nfs", "read", None, 1, None, None, 4.0),
    ],
  )


def test_consumed_series_stage_and_flush():
  cs.begin("j1")
  cs.begin("j2")
  with cs.consumer_stage("metrics"):
    cs.record("j1", [("host_cpu", "user"), ("host_cpu", "user")])
  cs.record("j1", [("nfs", "read")])  # untagged -> other
  cs.record("j2", [("host_mem", "used")], stage="job_detail")
  cs.record("never-begun", [("x", "y")])
  client = _Client()
  assert cs.flush("j1", client, now=100) == 2
  assert len(client.pipes) == 1, "one pipeline per jid"
  pipe = client.pipes[0]
  assert pipe.executed == 1
  assert sorted(pipe.hsets) == [
    ("metrics_consumed:v1:metrics", {"host_cpu\tuser": 100}),
    ("metrics_consumed:v1:other", {"nfs\tread": 100}),
  ]
  assert {k for k, _ in pipe.expires} == {
    "metrics_consumed:v1:metrics",
    "metrics_consumed:v1:other",
  }
  assert not cs.is_active("j1"), "flush closes the jid"
  assert cs.is_active("j2"), "other jids are untouched"
  assert cs.take("j2") == {"job_detail": [("host_mem", "used")]}
  assert cs.flush("never-begun", client) == 0


def test_consumed_series_stage_is_per_thread():
  seen = {}
  barrier = threading.Barrier(2)

  def _worker(name):
    with cs.consumer_stage(name):
      barrier.wait()
      seen[name] = cs.current_stage()

  threads = [threading.Thread(target=_worker, args=(n,)) for n in ("a", "b")]
  for t in threads:
    t.start()
  for t in threads:
    t.join()
  assert seen == {"a": "a", "b": "b"}
  assert cs.current_stage() == cs.STAGE_OTHER


def test_consumed_series_discard_and_valkey_error_swallowed(caplog):
  cs.begin("j1")
  cs.record("j1", [("a", "b")], stage="metrics")
  cs.discard("j1")
  assert cs.flush("j1", _Client()) == 0
  cs.begin("j2")
  cs.record("j2", [("a", "b")], stage="metrics")
  assert cs.flush("j2", _Client(fail=True)) == 0
  assert not cs.is_active("j2")
  cs.begin("j3")
  cs.record("j3", [("a", "b")], stage="metrics")
  assert cs.flush("j3", _Client(fail=True)) == 0
  warnings = [r for r in caplog.records if "consumed_series" in r.message]
  assert len(warnings) == 1, "a Valkey failure is logged once per process"


def test_consumed_series_caps_and_merge(monkeypatch):
  monkeypatch.setattr(cs, "MAX_PAIRS_PER_JID", 3)
  monkeypatch.setattr(cs, "MAX_ACTIVE_JIDS", 2)
  cs.begin("j1")
  cs.record("j1", [("t", str(i)) for i in range(10)], stage="metrics")
  assert len(cs.take("j1")["metrics"]) == 3
  cs.begin("a")
  cs.begin("b")
  cs.begin("c")
  assert not cs.is_active("a") and cs.is_active("b") and cs.is_active("c")
  cs.merge(
    "c",
    {
      "type_detail": [["host_cpu", "user"], ("bad",)],
      "unknown_stage": [["x", "y"]],
    },
  )
  assert cs.take("c") == {"type_detail": [("host_cpu", "user")]}


def test_pair_field_roundtrip():
  field = cs.pair_field("lustre_llite", "vfs_read_bytes")
  assert cs.parse_pair_field(field.encode()) == (
    "lustre_llite",
    "vfs_read_bytes",
  )
  assert cs.parse_pair_field("\tx") is None


_REDUCTIONS = {
  "sum_per_sample": (
    lambda ix: ix.sum_per_sample("host_cpu", ["user"], "arc"),
    {("host_cpu", "user")},
  ),
  "sum_per_sample_all_events": (
    lambda ix: ix.sum_per_sample("host_cpu", None, "arc"),
    {("host_cpu", "user"), ("host_cpu", "nice")},
  ),
  "raw_rows": (
    lambda ix: ix.raw_rows("nvidia_gpu", ["gpu_util"], "value"),
    {("nvidia_gpu", "gpu_util")},
  ),
  "delta_sum_by_event": (
    lambda ix: ix.delta_sum_by_event("nfs", ["read", "write"]),
    {("nfs", "read")},
  ),
  "gpu_group_stats": (
    lambda ix: ix.gpu_group_stats("nvidia_gpu", ["gpu_util"]),
    {("nvidia_gpu", "gpu_util")},
  ),
  "metrics_pivot": (
    lambda ix: ix.metrics_pivot("host_cpu", ["nice", "missing"]),
    {("host_cpu", "nice")},
  ),
}


@pytest.mark.parametrize("name", sorted(_REDUCTIONS))
def test_frame_index_every_selection_records(name):
  call, expected = _REDUCTIONS[name]
  index = kn.FrameIndex(_frame())
  cs.begin("j1")
  with cs.consumer_stage("metrics"):
    call(index)
  assert set(cs.take("j1").get("metrics", [])) == expected


def test_frame_index_reduction_list_is_complete():
  """Drift guard: a new public reduction must be added to _REDUCTIONS."""
  public = {
    name
    for name, fn in inspect.getmembers(kn.FrameIndex, inspect.isfunction)
    if not name.startswith("_")
    and "events" in inspect.signature(fn).parameters
    and name != "series_ids"
  }
  covered = {n.removesuffix("_all_events") for n in _REDUCTIONS}
  assert public == covered


def test_frame_index_selects_only_through_select():
  """Drift guard: series ids are resolved only inside FrameIndex._select."""
  tree = ast.parse(Path(kn.__file__).read_text())
  cls = next(
    n
    for n in tree.body
    if isinstance(n, ast.ClassDef) and n.name == "FrameIndex"
  )
  offenders = []
  for fn in cls.body:
    if not isinstance(fn, ast.FunctionDef) or fn.name in {
      "_select",
      "series_ids",
    }:
      continue
    for node in ast.walk(fn):
      if isinstance(node, ast.Attribute) and node.attr in {
        "series_ids",
        "series_ids_for_type",
      }:
        offenders.append(fn.name)
  assert offenders == []


def test_unopened_jid_records_nothing():
  kn.FrameIndex(_frame("other")).sum_per_sample("host_cpu", None, "arc")
  assert cs._active == {}


def test_type_detail_dropped_recorded():
  jt = table_from_records(
    [
      (1 * US, "h1", "host_cpu", "user", None, 1.0, 2.0, None),
      (2 * US, "h1", "host_cpu", "user", None, 1.0, 3.0, None),
      (1 * US, "h1", "host_cpu", "nice", None, 1.0, 2.0, None),
    ],
    jid="jt1",
  )
  cs.begin("jt1")
  with cs.consumer_stage("type_detail"):
    series = build_type_detail_series(
      jt, "host_cpu", [("user", "%"), ("nice", "%")], ["host_cpu"]
    )
  assert list(series.frame.columns) == ["user"]
  rec = cs.take("jt1")
  assert rec == {
    "type_detail": [("host_cpu", "user")],
    "type_detail_dropped": [("host_cpu", "nice")],
  }
  assert np.isfinite(series.frame.columns["user"]).all()


def test_golden_job_records_expected_stages(monkeypatch):
  """A metrics compute plus a type panel records both stages; flush once."""
  jt = table_from_records(
    [
      (1 * US, "h1", "host_cpu", "user", None, 1.0, 2.0, 1.0),
      (2 * US, "h1", "host_cpu", "user", None, 1.0, 3.0, 1.0),
    ],
    jid="g1",
  )
  cs.begin("g1")
  with cs.consumer_stage("metrics"):
    jt.index.sum_per_sample("host_cpu", ["user"], "arc")
  with cs.consumer_stage("type_detail"):
    build_type_detail_series(jt, "host_cpu", [("user", "%")], ["host_cpu"])
  client = _Client()
  assert cs.flush("g1", client, now=7) == 2
  assert len(client.pipes) == 1
  keys = sorted(k for k, _ in client.pipes[0].hsets)
  assert keys == [
    "metrics_consumed:v1:metrics",
    "metrics_consumed:v1:type_detail",
  ]
  assert cs.flush("g1", client) == 0, "second flush writes nothing"


def test_compute_and_prewarm_flushes_on_success_and_discards_on_failure(
  monkeypatch,
):
  from hpcperfstats.analysis.metrics import update_metrics as um

  monkeypatch.setattr(um, "log_print", lambda *a, **k: None)
  flushed, discarded = [], []
  monkeypatch.setattr(um.consumed_series, "flush", flushed.append)
  real_discard = cs.discard
  monkeypatch.setattr(
    um.consumed_series,
    "discard",
    lambda jid: (discarded.append(jid), real_discard(jid)),
  )

  class _M:
    def __init__(self, ok):
      self.ok = ok

    def ensure_pool(self, pool_kind="metrics-pool"):
      return None

    def run(self, job_list, pool=None):
      assert cs.is_active(job_list[0].jid), "jid opened before compute"
      return [{"jid": job_list[0].jid, "ok": self.ok, "status": "x"}]

  class _P:
    def run_for_jid(self, jid, shared_context=None):
      return {"prewarm_total_s": 0.0, "detail_s": 0.0, "plots_s": 0.0}

  um._compute_and_prewarm_jid(_M(True), _P(), SimpleNamespace(jid="ok1"), None)
  um._compute_and_prewarm_jid(
    _M(False), _P(), SimpleNamespace(jid="bad1"), None
  )
  assert flushed == ["ok1"]
  assert discarded == ["bad1"]
  assert not cs.is_active("bad1")


def test_prewarm_child_ships_consumed_records(monkeypatch):
  from hpcperfstats.analysis.metrics.lib import prewarm_process_pool as ppp
  from hpcperfstats.site.lib.machine import (
    job_detail_artifacts as jda,
    job_plot_artifacts as jpa,
  )

  def _detail(jid, context=None):
    cs.record(jid, [("host_cpu", "user")], stage="job_detail")

  monkeypatch.setattr(jda, "persist_job_detail_artifacts_for_jid", _detail)
  monkeypatch.setattr(
    jpa, "persist_job_plot_artifacts_for_jid", lambda jid, context=None: None
  )
  monkeypatch.setattr(
    "django.db.close_old_connections", lambda: None, raising=False
  )
  out = ppp.run_prewarm_in_child("c1", None)
  assert out["ok"] is True
  assert out["consumed"] == {"job_detail": [("host_cpu", "user")]}
  assert not cs.is_active("c1")
