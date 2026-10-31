"""NumPy kernels reproduce the pandas/SQL semantics of the update_metrics reads."""

import numpy as np
import pandas as pd

from hpcperfstats.analysis.metrics.lib.columnar import kernels as kn
from hpcperfstats.analysis.metrics.lib.columnar.jobframe import JobFrame
from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
  MAX_SANE_COUNTER_RATE,
)
from hpcperfstats.lib.dcgm_blank import DCGM_FP64_BLANK

US = 1_000_000


def _random_records(seed: int, *, shared: bool = False, n: int = 600):
  """Random (time, host, type, event, dev, owned, value, arc, delta) rows."""
  rng = np.random.default_rng(seed)
  hosts = ["h1", "h2", "h3"]
  series = [
    ("host_cpu", "user"),
    ("host_cpu", "nice"),
    ("host_cpu", "idle"),
    ("host_mem", "mem_used"),
    ("nvidia_gpu", "gpu_util"),
    ("nvidia_gpu", "gpu_count"),
    ("nfs", "normal_read"),
  ]
  devs_by_type = {
    "host_cpu": ["0", "1", "2"],
    "host_mem": ["0", "1"],
    "nvidia_gpu": ["0", "1"],
    "nfs": [None],
  }
  recs = []
  seen = set()
  for _ in range(n):
    t = int(rng.integers(0, 40)) * 30 * US
    host = hosts[int(rng.integers(0, 3))]
    typ, ev = series[int(rng.integers(0, len(series)))]
    dev = devs_by_type[typ][int(rng.integers(0, len(devs_by_type[typ])))]
    owned = 1
    if shared and typ in ("host_mem", "nfs"):
      kind = int(rng.integers(0, 3))
      if kind == 0:
        owned = 0  # hardware row of another tenant / host-wide
      elif kind == 1:
        dev = f"j:{int(rng.integers(1, 4))}"
        owned = int(dev == "j:1")
    key = (t, host, typ, ev, dev)
    if key in seen:
      continue
    seen.add(key)

    def _val():
      r = rng.random()
      if r < 0.08:
        return None
      if r < 0.12:
        return 1e14  # above the rate ceiling
      if r < 0.16:
        return float(-rng.random() * 10)
      if r < 0.19:
        return float(DCGM_FP64_BLANK)
      return float(rng.random() * 100)

    recs.append((t, host, typ, ev, dev, owned, _val(), _val(), _val()))
  return recs


def _df(recs, frame):
  df = pd.DataFrame(
    recs,
    columns=[
      "t",
      "host",
      "type",
      "event",
      "dev",
      "owned",
      "value",
      "arc",
      "delta",
    ],
  )
  df["dev"] = df["dev"].fillna("")
  df["host_id"] = df["host"].map({h: i for i, h in enumerate(frame.hosts)})
  return df


def _sql_sum(df, typ, events, col, *, host_wide, nonneg=False):
  sub = df[(df.type == typ) & df.event.isin(events)]
  if not host_wide:
    sub = sub[sub.owned == 1]
  sub = sub[sub[col].notna() & (sub[col].abs() < MAX_SANE_COUNTER_RATE)]
  if nonneg:
    sub = sub[sub[col] >= 0]
  if host_wide:
    rows = []
    for (h, t, _e), g in sub.groupby(["host_id", "t", "event"]):
      hw = g[~g.dev.str.startswith("j:")]
      if len(hw):
        rows.append((h, t, hw[col].sum()))
      else:
        rows.append((h, t, g[col].max()))
    sub = pd.DataFrame(rows, columns=["host_id", "t", col])
  if sub.empty:
    return sub
  return (
    sub.groupby(["host_id", "t"], as_index=False)[col]
    .sum()
    .sort_values(["host_id", "t"])
  )


def test_sum_per_sample_sql_matches_pandas_owned_only():
  recs = _random_records(3)
  frame = JobFrame.from_records("1", recs)
  idx = kn.FrameIndex(frame)
  df = _df(recs, frame)
  for col, nonneg in (("arc", False), ("value", True)):
    got = idx.sum_per_sample(
      "host_cpu", ["user", "nice"], col, conv=0.5, nonnegative=nonneg
    )
    exp = _sql_sum(
      df, "host_cpu", ["user", "nice"], col, host_wide=False, nonneg=nonneg
    )
    assert got.host_id.tolist() == exp.host_id.tolist()
    assert got.time_us.tolist() == exp.t.tolist()
    np.testing.assert_allclose(
      got.sum_val, exp[col].to_numpy() * 0.5, rtol=1e-12
    )


def test_sum_per_sample_sql_host_wide_collapse_rule():
  recs = _random_records(5, shared=True)
  frame = JobFrame.from_records("1", recs)
  idx = kn.FrameIndex(frame, shared=True, residual_types=["host_mem", "nfs"])
  assert idx.host_wide("host_mem") and not idx.host_wide("host_cpu")
  df = _df(recs, frame)
  got = idx.sum_per_sample("host_mem", ["mem_used"], "value")
  exp = _sql_sum(df, "host_mem", ["mem_used"], "value", host_wide=True)
  assert got.host_id.tolist() == exp.host_id.tolist()
  assert got.time_us.tolist() == exp.t.tolist()
  np.testing.assert_allclose(got.sum_val, exp["value"].to_numpy(), rtol=1e-12)
  # Job-scoped type on the same shared frame stays owned-only.
  got_cpu = idx.sum_per_sample("host_cpu", ["user"], "arc")
  exp_cpu = _sql_sum(df, "host_cpu", ["user"], "arc", host_wide=False)
  np.testing.assert_allclose(
    got_cpu.sum_val, exp_cpu["arc"].to_numpy(), rtol=1e-12
  )


def test_sum_per_sample_orm_blank_filter_by_dev_and_coalesce_zero():
  recs = [
    (1 * US, "h1", "nvidia_gpu", "gpu_util", "0", 1, 10.0, None, None),
    (
      1 * US,
      "h1",
      "nvidia_gpu",
      "gpu_util",
      "1",
      1,
      DCGM_FP64_BLANK,
      None,
      None,
    ),
    (2 * US, "h1", "nvidia_gpu", "gpu_util", "0", 1, None, None, None),
    (2 * US, "h1", "nvidia_gpu", "gpu_util", "1", 1, 5.0, None, None),
    (3 * US, "h1", "nvidia_gpu", "gpu_util", "0", 1, -4.0, None, None),
    (3 * US, "h1", "nvidia_gpu", "gpu_util", "1", 1, 6.0, None, None),
  ]
  frame = JobFrame.from_records("1", recs)
  idx = kn.FrameIndex(frame)
  # Blank filter removes the blank row; NULL rows do not contribute.
  t = idx.sum_per_sample(
    "nvidia_gpu",
    ["gpu_util"],
    "value",
    mode=kn.MODE_ORM,
    reject_dcgm_blank=True,
  )
  assert t.time_us.tolist() == [1 * US, 2 * US, 3 * US]
  assert t.sum_val.tolist() == [10.0, 5.0, 2.0]
  # Per-device grouping keeps a NULL-only group with Coalesce 0.
  d = idx.sum_per_sample(
    "nvidia_gpu", ["gpu_util"], "value", mode=kn.MODE_ORM, by_dev=True
  )
  dev_names = [frame.devs[abs(i)] for i in d.dev_id]
  assert list(zip(d.time_us.tolist(), dev_names, strict=True)) == [
    (1 * US, "0"),
    (1 * US, "1"),
    (2 * US, "0"),
    (2 * US, "1"),
    (3 * US, "0"),
    (3 * US, "1"),
  ]
  assert d.sum_val.tolist() == [10.0, DCGM_FP64_BLANK, 0.0, 5.0, -4.0, 6.0]
  # Nonnegative FILTER keeps the group, sums only >= 0.
  nn = idx.sum_per_sample(
    "nvidia_gpu", ["gpu_util"], "value", mode=kn.MODE_ORM, nonnegative=True
  )
  assert nn.sum_val.tolist()[2] == 6.0
  # Affinity dev filter: only device "1" allowed; empty list excludes all.
  idx1 = kn.FrameIndex(frame, gpu_affinity_devs=["1"])
  a = idx1.sum_per_sample(
    "nvidia_gpu",
    ["gpu_util"],
    "value",
    mode=kn.MODE_ORM,
    by_dev=True,
    dev_filter=True,
  )
  assert a.sum_val.tolist() == [DCGM_FP64_BLANK, 5.0, 6.0]
  idx0 = kn.FrameIndex(frame, gpu_affinity_devs=[])
  assert idx0.sum_per_sample(
    "nvidia_gpu", ["gpu_util"], "value", mode=kn.MODE_ORM, dev_filter=True
  ).empty


def test_host_time_keys_and_distinct_count_match_pandas():
  recs = _random_records(7, shared=True)
  frame = JobFrame.from_records("1", recs)
  idx = kn.FrameIndex(frame, shared=True, residual_types=["host_mem", "nfs"])
  df = _df(recs, frame)
  exp = (
    df[df.owned == 1][["host_id", "t"]]
    .drop_duplicates()
    .sort_values(["host_id", "t"])
  )
  h, t = idx.host_time_keys()
  assert h.tolist() == exp.host_id.tolist()
  assert t.tolist() == exp.t.tolist()
  assert idx.distinct_time_count_per_host_sum() == int(
    df[df.owned == 1].groupby("host")["t"].nunique().sum()
  )


def test_delta_sum_by_event_both_scopes():
  recs = _random_records(11, shared=True)
  frame = JobFrame.from_records("1", recs)
  df = _df(recs, frame)
  lim = 1e17
  # Residual (host-wide) branch: collapse then sum.
  idx = kn.FrameIndex(frame, shared=True, residual_types=["host_mem", "nfs"])
  got = idx.delta_sum_by_event("nfs", ["normal_read"])
  sub = df[(df.type == "nfs") & df.delta.notna() & (df.delta.abs() < lim)]
  vals = []
  for _k, g in sub.groupby(["host_id", "t"]):
    hw = g[~g.dev.str.startswith("j:")]
    vals.append(hw.delta.sum() if len(hw) else g.delta.max())
  if vals:
    np.testing.assert_allclose(
      got["normal_read"], float(np.sum(vals)), rtol=1e-12
    )
  else:
    assert got == {}
  # Job-scoped branch: owned rows strictly inside (-lim, lim).
  idx2 = kn.FrameIndex(frame)
  got2 = idx2.delta_sum_by_event("nfs", ["normal_read"])
  sub2 = df[(df.type == "nfs") & (df.owned == 1) & df.delta.notna()]
  sub2 = sub2[(sub2.delta > -lim) & (sub2.delta < lim)]
  np.testing.assert_allclose(got2["normal_read"], sub2.delta.sum(), rtol=1e-12)


def test_gpu_group_stats_match_pandas_with_affinity():
  recs = _random_records(13)
  frame = JobFrame.from_records("1", recs)
  df = _df(recs, frame)
  idx = kn.FrameIndex(frame, gpu_affinity_devs=["0"])
  st = idx.gpu_group_stats("nvidia_gpu", ["gpu_util"])
  sub = df[
    (df.type == "nvidia_gpu") & (df.event == "gpu_util") & (df.dev == "0")
  ]
  sub = sub[sub.value.notna() & (sub.value < DCGM_FP64_BLANK)]
  exp = (
    sub.groupby(["host_id", "dev"])["value"]
    .agg(["count", "max", "mean"])
    .reset_index()
  )
  assert st.host_id.tolist() == exp.host_id.tolist()
  assert st.count.tolist() == exp["count"].tolist()
  np.testing.assert_allclose(st.vmax, exp["max"].to_numpy())
  np.testing.assert_allclose(st.vmean, exp["mean"].to_numpy(), rtol=1e-12)
  # Host-only grouping (gpu_count max per host).
  st2 = idx.gpu_group_stats(
    "nvidia_gpu", ["gpu_count"], group_dev=False, group_event=False
  )
  sub2 = df[
    (df.type == "nvidia_gpu") & (df.event == "gpu_count") & (df.dev == "0")
  ]
  sub2 = sub2[sub2.value.notna() & (sub2.value < DCGM_FP64_BLANK)]
  exp2 = sub2.groupby("host_id")["value"].max()
  assert st2.host_id.tolist() == exp2.index.tolist()
  np.testing.assert_allclose(st2.vmax, exp2.to_numpy())


def test_metrics_pivot_matches_job_for_metrics_pandas():
  recs = _random_records(17)
  frame = JobFrame.from_records("1", recs)
  df = _df(recs, frame)
  events = ["user", "nice", "idle", "ghost"]
  p = kn.FrameIndex(frame).metrics_pivot("host_cpu", events)
  owned = df[df.owned == 1]
  times = np.sort(owned.t.unique())
  np.testing.assert_array_equal(p["times_s"], (times // US).astype(np.float64))
  sub = owned[owned.type == "host_cpu"]
  cl = sub.groupby(["t", "event"])["value"].mean().unstack()
  cl = cl.reindex(index=times).reindex(columns=events)
  np.testing.assert_allclose(
    p["cluster_value"], cl.to_numpy(dtype=float), equal_nan=True
  )
  cla = sub.groupby(["t", "event"])["arc"].mean().unstack()
  cla = cla.reindex(index=times).reindex(columns=events)
  np.testing.assert_allclose(
    p["cluster_arc"], cla.to_numpy(dtype=float), equal_nan=True
  )
  for k, hid in enumerate(p["host_ids"].tolist()):
    hdf = sub[sub.host_id == hid]
    piv = hdf.pivot_table(
      index="t", columns="event", values="value", aggfunc="mean"
    )
    piv = piv.reindex(index=times).reindex(columns=events)
    np.testing.assert_allclose(
      p["per_host"][k], piv.to_numpy(dtype=float), equal_nan=True
    )


def test_bucketed_job_value_matches_job_arc_pandas():
  recs = _random_records(19)
  frame = JobFrame.from_records("1", recs)
  df = _df(recs, frame)
  idx = kn.FrameIndex(frame)
  agg = idx.sum_per_sample("host_cpu", ["user", "nice"], "arc")
  got_mean = kn.bucketed_job_value(agg.host_id, agg.time_us, agg.sum_val, 0.01)
  got_sum = kn.bucketed_job_value(
    agg.host_id, agg.time_us, agg.sum_val, 0.01, host_aggregate="sum"
  )
  per_time = _sql_sum(df, "host_cpu", ["user", "nice"], "arc", host_wide=False)
  per_time["time"] = pd.to_datetime(per_time.t, unit="us", utc=True)
  per_time["bucket"] = per_time["time"].dt.floor("5min")
  g = per_time.groupby(["host_id", "bucket"], as_index=False)["arc"].mean()
  g["arc"] *= 0.01
  keep = []
  for _h, sub in g.groupby("host_id", sort=False):
    keep.extend(list(sub.index) if len(sub) <= 1 else list(sub.index[1:]))
  g = g.loc[keep]
  per_host = g.groupby("host_id")["arc"].mean()
  np.testing.assert_allclose(got_mean, per_host.mean(), rtol=1e-12)
  np.testing.assert_allclose(got_sum, per_host.sum(), rtol=1e-12)
  assert (
    kn.bucketed_job_value(
      np.array([], dtype=np.int64),
      np.array([], dtype=np.int64),
      np.array([]),
      1.0,
    )
    is None
  )
