"""AMD DF beat keys count 64-byte DRAM beats, not bytes.

The monitor's ``amd_x86_uncore_df_{rome,milan,genoa,turin}`` keys hold DF
data beats (UMC CAS counts on LIKWID Turin) in every monitor era: neither
the LIKWID collectors nor the x86perf reader scale them. Older monitors
emit one ``df`` row with ``dram_chan0_bytes`` .. ``dram_chan3_bytes``; the
current monitor emits one ``chan<N>`` row per DRAM channel with
``dram_data_beats`` (Zen2 / Zen3) or ``dram_read_beats`` /
``dram_write_beats`` (Zen4 / Zen5), sentinel values on the keys a row does
not carry. AMD memory bandwidth (``avg_mbw``, the roofline bandwidth points
and the Summary ``amd_mbw`` series) sums every beat key over every row and
multiplies by ``AMD_DF_BYTES_PER_BEAT``. The historical ``MBW_CHANNEL_*``
path keeps its own factor (2 / GiB).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from hpcperfstats.analysis.metrics.lib import metrics
from hpcperfstats.analysis.metrics.lib.columnar.roofline_series import (
  cpu_roofline_points,
)
from hpcperfstats.analysis.metrics.lib.columnar.summary_series import (
  build_summary_series,
)
from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
)
from hpcperfstats.dbload.lib.monitor_naming import (
  canonical as canon,
  legacy as leg,
)
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
  amd_df_bw_event_conv_tries,
)

_T0 = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
_GIB = 1024**3
# Beats per second on each of the four DF channels.
_BEATS_PER_S = 1.0e8
_BEAT_GIB_S = 4 * _BEATS_PER_S * 64 / _GIB
_OLD_DF_KEYS = tuple(f"dram_chan{i}_bytes" for i in range(4))
_NOT_PRESENT = -2.0
_NOT_BUILT = -10.0


def _records(rates, typ, minutes=15, dev="-"):
  """One sample per minute on host c1 with constant per-key rates.

  A negative rate is a monitor sentinel: the row carries it as ``value``
  with a NULL ``arc``, as ingest stores sentinel samples.
  """
  rows = []
  for i in range(minutes + 1):
    t = _T0 + timedelta(minutes=i)
    for event, rate in rates.items():
      if rate < 0:
        rows.append((t, "c1", typ, event, dev, rate, None, None))
      else:
        rows.append((t, "c1", typ, event, dev, rate * 60.0 * i, rate, None))
  return rows


def _beat_rows(typ="amd_x86_uncore_df_genoa"):
  """Old-shape DF row ``df``: ``_BEATS_PER_S`` on each ``dram_chanN_bytes``."""
  return _records(dict.fromkeys(_OLD_DF_KEYS, _BEATS_PER_S), typ, dev="df")


def _milan_chan_rows():
  """New-shape Milan: 8 ``chan<N>`` rows of ``dram_data_beats``.

  Channel N runs at ``(N + 1) * _BEATS_PER_S``; the Zen4 read / write keys
  are NOT_PRESENT on every row.
  """
  rows = []
  for n in range(8):
    rates = {
      "dram_data_beats": (n + 1) * _BEATS_PER_S,
      "dram_read_beats": _NOT_PRESENT,
      "dram_write_beats": _NOT_PRESENT,
    }
    rows += _records(rates, "amd_x86_uncore_df_milan", dev=f"chan{n}")
  return rows


_MILAN_GIB_S = sum(range(1, 9)) * _BEATS_PER_S * 64 / _GIB


def _genoa_chan_rows():
  """New-shape Genoa: 12 ``chan<N>`` rows, read / write beats on 0-3.

  Channels 0-3 read at ``_BEATS_PER_S`` and write at half that, with
  ``dram_data_beats`` NOT_PRESENT; channels 4-11 are NOT_BUILT on every key.
  """
  rows = []
  for n in range(12):
    if n < 4:
      rates = {
        "dram_data_beats": _NOT_PRESENT,
        "dram_read_beats": _BEATS_PER_S,
        "dram_write_beats": _BEATS_PER_S / 2,
      }
    else:
      rates = dict.fromkeys(
        ("dram_data_beats", "dram_read_beats", "dram_write_beats"),
        _NOT_BUILT,
      )
    rows += _records(rates, "amd_x86_uncore_df_genoa", dev=f"chan{n}")
  return rows


_GENOA_GIB_S = 4 * 1.5 * _BEATS_PER_S * 64 / _GIB


def _avg_mbw(monkeypatch, table):
  """Run ``compute_metrics`` for ``avg_mbw`` only and return its row."""
  monkeypatch.setattr(metrics, "open_job_table", lambda job, **_kw: table)
  monkeypatch.setattr(
    metrics, "gpu_summary_tuple", lambda jt: (None, None, None, None)
  )
  monkeypatch.setattr(
    metrics, "compute_job_detail_fsio_metric_rows", lambda jt: []
  )
  monkeypatch.setattr(
    metrics, "_in_window_telemetry_bounds_for_job", lambda job: (None, None)
  )
  m = metrics.Metrics()
  m.simple_metrics_list = {"avg_mbw": m.simple_metrics_list["avg_mbw"]}
  m.complex_metrics_list = []
  job = SimpleNamespace(
    jid="amddf1",
    telemetry_first_time=None,
    telemetry_last_time=None,
    host_data_schema_json=None,
  )
  rows = m.compute_metrics(job)["rows"]
  return next(r for r in rows if r["metric"] == "avg_mbw")


def test_conv_tries_scale_df_beats_by_64_and_keep_legacy_mbw():
  """DF beat keys convert at 64 B / GiB; MBW_CHANNEL stays at 2 / GiB."""
  assert canon.AMD_DF_BYTES_PER_BEAT == 64
  tries = amd_df_bw_event_conv_tries()
  assert tries[0][0] == canon.AMD_DF_BEAT_EVENTS
  assert dict(tries) == {
    canon.AMD_DF_BEAT_EVENTS: pytest.approx(64.0 / _GIB),
    leg.LEGACY_AMD_DF_MBW_CHANNEL_EVENTS: pytest.approx(2.0 / _GIB),
  }


def test_beat_events_cover_new_and_old_df_keys():
  """One beat tuple: the per-channel keys and the old ``df`` row keys."""
  assert set(canon.AMD_DF_BEAT_EVENTS) == {
    "dram_data_beats",
    "dram_read_beats",
    "dram_write_beats",
    *_OLD_DF_KEYS,
  }


def test_avg_mbw_family_df_beats_are_64_bytes(monkeypatch):
  """avg_mbw = sum of beat rates x 64 B, in GiB/s."""
  row = _avg_mbw(monkeypatch, table_from_records(_beat_rows()))

  assert row["type"] == "amd_x86_uncore_df_genoa"
  assert row["value"] == pytest.approx(_BEAT_GIB_S)


def test_avg_mbw_legacy_mbw_channel_factor_unchanged(monkeypatch):
  """Historical amd_x86_uncore_df MBW_CHANNEL rows keep 2 / GiB."""
  rates = dict.fromkeys(leg.LEGACY_AMD_DF_MBW_CHANNEL_EVENTS, 1.0e9)
  table = table_from_records(_records(rates, canon.AMD_DF_TYPE))

  row = _avg_mbw(monkeypatch, table)

  n = len(leg.LEGACY_AMD_DF_MBW_CHANNEL_EVENTS)
  assert row["value"] == pytest.approx(n * 1.0e9 * 2.0 / _GIB)


def test_summary_amd_mbw_series_uses_64_bytes_per_beat():
  """Summary amd_mbw column: beat rates x 64 B / GiB per sample."""
  frame = build_summary_series(table_from_records(_beat_rows())).frame

  vals = np.asarray(frame.columns["amd_mbw"], dtype=float)
  vals = vals[np.isfinite(vals)]
  assert vals.size
  assert np.allclose(vals, _BEAT_GIB_S)


def test_amd_roofline_bandwidth_uses_64_bytes_per_beat():
  """AMD roofline bandwidth points: beat rates x 64 B / GiB."""
  flops = _records({"fp_ops_retired": 2.0e12}, canon.HOST_CPU_HW_TYPE)
  points, reason = cpu_roofline_points(
    table_from_records(flops + _beat_rows("amd_x86_uncore_df_milan"))
  )

  assert reason is None
  assert np.allclose(points.bw_gb, _BEAT_GIB_S)


def test_avg_mbw_milan_sums_all_eight_channel_rows(monkeypatch):
  """Milan chan0..chan7 dram_data_beats rows are summed, not deduped."""
  row = _avg_mbw(monkeypatch, table_from_records(_milan_chan_rows()))

  assert row["type"] == "amd_x86_uncore_df_milan"
  assert row["value"] == pytest.approx(_MILAN_GIB_S)


def test_avg_mbw_genoa_read_plus_write_excludes_sentinel_rows(monkeypatch):
  """Genoa read + write beats on chan0-3; NOT_BUILT chan4-11 add nothing."""
  row = _avg_mbw(monkeypatch, table_from_records(_genoa_chan_rows()))

  assert row["type"] == "amd_x86_uncore_df_genoa"
  assert row["value"] == pytest.approx(_GENOA_GIB_S)


def test_summary_amd_mbw_series_new_channel_rows():
  """Summary amd_mbw sums every chan<N> row's beat keys x 64 B / GiB."""
  frame = build_summary_series(table_from_records(_genoa_chan_rows())).frame

  vals = np.asarray(frame.columns["amd_mbw"], dtype=float)
  vals = vals[np.isfinite(vals)]
  assert vals.size
  assert np.allclose(vals, _GENOA_GIB_S)


def test_amd_roofline_bandwidth_new_channel_rows():
  """Roofline bandwidth points sum the Milan chan<N> rows x 64 B / GiB."""
  flops = _records({"fp_ops_retired": 2.0e12}, canon.HOST_CPU_HW_TYPE)
  points, reason = cpu_roofline_points(
    table_from_records(flops + _milan_chan_rows())
  )

  assert reason is None
  assert np.allclose(points.bw_gb, _MILAN_GIB_S)


def test_metrics_contract_bumped_for_df_channel_rows():
  """Contract 16: per-channel DF rows (Milan bandwidth about 2x, no backfill)."""
  assert metrics.METRICS_CONTRACT_VERSION >= 16
