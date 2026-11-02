"""COPY fetch: scope SQL, chunking, and statement-timeout bisect parity."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from django.db.utils import OperationalError

from hpcperfstats.analysis.metrics.lib.columnar import copy_fetch
from hpcperfstats.analysis.metrics.lib.columnar.k0_decode import (
  encode_copy_binary,
)

T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
T0_US = int(T0.timestamp() * 1_000_000)


@pytest.fixture(autouse=True)
def _no_db_connection_housekeeping(monkeypatch):
  """The chunk retry helper closes stale Django connections between chunks;
  these unit tests use a fake COPY runner and must not touch the database."""
  from hpcperfstats.analysis.metrics.lib.gen import jid_table as jt_mod

  monkeypatch.setattr(jt_mod, "close_old_connections", lambda: None)


def _fake_table(hosts, *, shared=False, time_in=None):
  if time_in is not None:
    base = {"time__in": list(time_in), "host__in": list(hosts)}
  else:
    base = {
      "time__gte": T0,
      "time__lte": T0 + timedelta(hours=3),
      "host__in": list(hosts),
    }
  jt = SimpleNamespace(
    jid="77",
    acct_host_list=list(hosts),
    _base_filter=base,
    _shared_node=shared,
  )
  jt._host_data_time_filter_kwargs = lambda: (
    {"time__in": base["time__in"]}
    if "time__in" in base
    else {"time__gte": base["time__gte"], "time__lte": base["time__lte"]}
  )
  return jt


def _rows_for(hosts, tf):
  """Synthetic server: one row per host per minute inside the time filter."""
  lo = tf.get("time__gte") or tf.get("time__gt")
  hi = tf.get("time__lte") or tf.get("time__lt")
  out = []
  for host in hosts:
    t = lo
    while t <= hi:
      if (tf.get("time__gt") is not None and t == lo) or (
        tf.get("time__lt") is not None and t == hi
      ):
        t += timedelta(minutes=10)
        continue
      t_us = int(t.timestamp() * 1_000_000)
      out.append((t_us, host, "host_cpu", "user", None, 1.0, 2.0, 3.0))
      t += timedelta(minutes=10)
  return out


class _Server:
  def __init__(self, *, timeout_when_hosts_gt=0):
    self.calls = []
    self.timeout_when_hosts_gt = timeout_when_hosts_gt

  def __call__(self, sql, params):
    hosts = params[0]
    self.calls.append(list(hosts))
    if self.timeout_when_hosts_gt and len(hosts) > self.timeout_when_hosts_gt:
      raise OperationalError("canceling statement due to statement timeout")
    tf = {"time__gte": params[1], "time__lte": params[2]}
    if "h.time > %s" in sql:
      tf = {"time__gt": params[1], "time__lte": params[2]}
    if "h.time < %s" in sql:
      tf = {"time__gte": params[1], "time__lt": params[2]}
    return encode_copy_binary(_rows_for(hosts, tf))


def test_build_copy_sql_non_shared_scopes_to_job_rows():
  sql, params = copy_fetch.build_copy_sql(
    ["a", "b"],
    {"time__gte": T0, "time__lte": T0 + timedelta(hours=1)},
    "77",
    shared=False,
  )
  assert sql.startswith("COPY (SELECT h.time, h.host, h.type, h.event, h.dev")
  assert "owned" not in sql
  assert "(h.jid = %s OR h.dev = ('j:' || %s))" in sql
  assert sql.endswith("TO STDOUT (FORMAT BINARY)")
  assert params == [["a", "b"], T0, T0 + timedelta(hours=1), "77", "77"]
  assert "WHERE h.host = ANY(%s::text[])" in sql


def test_build_copy_sql_shared_reads_residual_types_host_wide():
  sql, params = copy_fetch.build_copy_sql(
    ["a"],
    {"time__gte": T0, "time__lt": T0 + timedelta(hours=1)},
    "77",
    shared=True,
    job_scoped_types=["host_cpu", "nvidia_gpu"],
  )
  assert "AS owned" in sql
  assert "OR NOT (h.type = ANY(%s::text[]))" in sql
  assert params[-1] == ["host_cpu", "nvidia_gpu"]
  assert params.count("77") == 4
  # Regression (edge_shared_b capture): parameters must follow placeholder
  # order - the SELECT ``owned`` column binds two jids before the WHERE
  # host / time binds, else PostgreSQL casts a timestamp to text[].
  assert sql.count("%s") == len(params)
  assert params[:3] == ["77", "77", ["a"]]
  assert params[3] == T0
  assert params[5:7] == ["77", "77"]
  select_part = sql.split(" FROM host_data h ")[0]
  assert select_part.count("%s") == 2


def test_build_copy_sql_time_in_uses_timestamptz_array():
  sql, params = copy_fetch.build_copy_sql(
    ["a"], {"time__in": [T0]}, "77", shared=False
  )
  assert "h.time = ANY(%s::timestamptz[])" in sql
  assert params[1] == [T0]


def test_fetch_chunks_hosts_and_time_and_merges_into_one_sorted_frame():
  hosts = [f"h{i:02d}" for i in range(20)]
  server = _Server()
  frame = copy_fetch.fetch_job_frame(
    _fake_table(hosts), batch_size=16, slice_s=3600, copy_runner=server
  )
  frame.check_contract()
  # 3 hours in 1h slices x 2 host batches (16 + 4) = 6 COPY calls.
  assert len(server.calls) == 6
  assert sorted(len(c) for c in server.calls) == [4, 4, 4, 16, 16, 16]
  expected_rows = sum(
    len(_rows_for(hosts, tf))
    for tf in (
      {"time__gte": T0, "time__lt": T0 + timedelta(hours=1)},
      {
        "time__gte": T0 + timedelta(hours=1),
        "time__lt": T0 + timedelta(hours=2),
      },
      {
        "time__gte": T0 + timedelta(hours=2),
        "time__lte": T0 + timedelta(hours=3),
      },
    )
  )
  assert len(frame) == expected_rows
  assert frame.hosts == hosts
  assert frame.series == [("host_cpu", "user")]


def test_statement_timeout_bisects_hosts_and_union_equals_unsplit_fetch():
  hosts = [f"h{i:02d}" for i in range(8)]
  clean = copy_fetch.fetch_job_frame(
    _fake_table(hosts), batch_size=16, slice_s=7200, copy_runner=_Server()
  )
  flaky = _Server(timeout_when_hosts_gt=2)
  split = copy_fetch.fetch_job_frame(
    _fake_table(hosts), batch_size=16, slice_s=7200, copy_runner=flaky
  )
  # The first attempt per slice carried 8 hosts and timed out; retries bisected
  # down to chunks of <= 2 hosts.
  assert any(len(c) == 8 for c in flaky.calls)
  assert max(len(c) for c in flaky.calls if len(c) <= 2) == 2
  assert len(split) == len(clean)
  for col in ("time_us", "host_id", "series_id", "dev_id", "owned"):
    assert np.array_equal(getattr(split, col), getattr(clean, col))
  for col in ("value", "arc", "delta"):
    assert np.array_equal(
      getattr(split, col), getattr(clean, col), equal_nan=True
    )
  assert split.hosts == clean.hosts and split.series == clean.series


def test_non_timeout_errors_propagate():
  def boom(sql, params):
    raise OperationalError("connection reset")

  with pytest.raises(OperationalError, match="connection reset"):
    copy_fetch.fetch_job_frame(
      _fake_table(["a"]), batch_size=16, slice_s=3600, copy_runner=boom
    )


def test_empty_hosts_or_window_gives_empty_frame():
  jt = _fake_table([])
  frame = copy_fetch.fetch_job_frame(jt, copy_runner=lambda s, p: b"")
  assert len(frame) == 0 and frame.jid == "77"


def test_fetch_caps_default_time_slice_for_cold_copy(monkeypatch):
  """hs04: 16 hosts x 1 h COPY (~320 s cold) burned two 120 s timeouts."""
  from hpcperfstats.analysis.metrics.lib.gen import jid_table as jt_mod

  seen = []
  real = jt_mod._iter_host_time_query_chunks

  def _spy(hosts, tkw, *, batch_size=None, slice_s=None):
    seen.append(slice_s)
    return real(hosts, tkw, batch_size=batch_size, slice_s=slice_s)

  monkeypatch.setattr(jt_mod, "_iter_host_time_query_chunks", _spy)
  monkeypatch.setattr(
    jt_mod.cfg, "get_metrics_plot_aggregate_time_slice_s", lambda: 3600
  )
  copy_fetch.fetch_job_frame(_fake_table(["a"]), copy_runner=_Server())
  monkeypatch.setattr(
    jt_mod.cfg, "get_metrics_plot_aggregate_time_slice_s", lambda: 300
  )
  copy_fetch.fetch_job_frame(_fake_table(["a"]), copy_runner=_Server())
  assert seen == [copy_fetch.COPY_MAX_TIME_SLICE_S, 300]
  assert copy_fetch.COPY_MAX_TIME_SLICE_S == 900


def test_build_copy_sql_time_slots_join_keeps_each_host_sample():
  """Large-job slots join an unnest of slot starts (per-host samples), and
  parameters still follow placeholder order on shared jobs."""
  slots = [T0, T0 + timedelta(minutes=10)]
  sql, params = copy_fetch.build_copy_sql(
    ["a", "b"],
    {"time__slots": slots, "time__slot_s": 60},
    "77",
    shared=True,
    job_scoped_types=["host_cpu"],
  )
  assert (
    "FROM unnest(%s::timestamptz[]) AS g(t0) JOIN host_data h ON "
    "h.time >= g.t0 AND h.time < g.t0 + %s * interval '1 second'"
  ) in sql
  assert "h.time = ANY" not in sql
  assert sql.count("%s") == len(params)
  assert params[:5] == ["77", "77", slots, 60, ["a", "b"]]
  assert params[-1] == ["host_cpu"]


def test_thinned_table_scales_fsio_delta_totals_by_slot_fraction():
  """Time slots keep slot/spacing of the window: summed ``delta`` totals
  (NFS / Lustre / BeeGFS bytes) scale back by spacing/slot; rates do not."""
  from types import SimpleNamespace

  from hpcperfstats.analysis.metrics.lib.columnar.job_table import (
    ColumnarJobTable,
  )
  from hpcperfstats.analysis.metrics.lib.columnar.testing import (
    table_from_records,
  )

  mb = 1024.0 * 1024.0
  rows = [
    (T0, "h1", "nfs", "normal_read", "", None, None, 2 * mb),
    (T0, "h1", "nfs", "normal_write", "", None, None, 1 * mb),
    (T0, "h1", "lustre_llite", "vfs_read_bytes", "", None, None, 10.0),
  ]
  plain = table_from_records(rows)
  assert plain.delta_scale == 1.0
  assert plain.get_nfs_delta_totals_mb() == [2.0, 1.0]
  slots = [T0, T0 + timedelta(minutes=3)]
  jt = SimpleNamespace(
    jid="1",
    host_list=["h1"],
    acct_host_list=["h1"],
    schema=plain.schema,
    start_time=T0,
    end_time=T0 + timedelta(minutes=6),
    _base_filter={
      "time__slots": slots,
      "time__slot_s": 60,
      "host__in": ["h1"],
    },
    _shared_node=False,
    units={},
  )
  thinned = ColumnarJobTable.from_jid_table(jt, frame=plain.frame)
  assert thinned.delta_scale == 3.0
  assert thinned.get_nfs_delta_totals_mb() == [6.0, 3.0]
  assert thinned.get_llite_delta_by_event() == {"vfs_read_bytes": 30.0}
