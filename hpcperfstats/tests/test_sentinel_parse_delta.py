"""Value sentinels through parse -> delta -> collapse -> arc (CPython twin).

The monitor emits ``-<n>`` for a value it could not read
(``hpcperfstats.lib.monitor_sentinel``). Sentinel rows keep the code in
``value`` with ``delta``/``arc`` NaN. A bridgeable gap (the counter kept
running) gives the next valid row ``delta = v_next - v_last_valid`` and an arc
over the real elapsed time; a chain-breaking gap gives NaN. Codon parity for
the same corpora lives in ``test_codon_feed_delta_collapse_arc.py``.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from hpcperfstats.dbload.lib import sync_timedb_parsing as stp

_IB_SCHEMA = "!host_ib rx_bytes,E,W=64,U=B"
_CPU_SCHEMA = "!cpu user,E,W=64,U=cs"
_HW_SCHEMA = "!host_cpu_hw instr_retired,E,W=48,U=1"


@pytest.fixture(autouse=True)
def _bridge_max_three(monkeypatch):
  monkeypatch.setattr(stp, "_sentinel_bridge_max_missed_samples", lambda: 3)


def _header(host: str = "hostA", build: str | None = None) -> list[str]:
  lines = ["$hpcperfstats 3.1", f"$hostname {host}"]
  if build is not None:
    lines.append(f"$build {build}")
  return lines


def _samples(typ: str, dev: str, points, host: str = "hostA") -> list[str]:
  """One device row per sample: ``points`` is ``[(time, token), ...]``."""
  out: list[str] = []
  for t, tok in points:
    out += [f"{t} J1 {host}", f"{typ} {dev} {tok}"]
  return out


def _run(lines: list[str], carry=None) -> pd.DataFrame:
  carry = carry if carry is not None else stp.DeltaCarryState()
  parser = stp.IncrementalStatsParser(0, delta_carry=carry)
  parser.feed_lines(list(lines))
  stats, _procs = parser.finish()
  cols = ["time", "host", "jid", "type", "dev", "event", "value", "wid"]
  cols += ["mult", "unit"]
  df = pd.DataFrame(stp.stats_payload_to_records(stats), columns=cols)
  out = stp.compute_deltas_and_arc_chunk(df, carry=carry)
  return out.sort_values("time").reset_index(drop=True)


def _row(out: pd.DataFrame, t: float) -> pd.Series:
  rows = out[out["time"] == t]
  assert len(rows) == 1, f"expected one row at t={t}, got {len(rows)}"
  return rows.iloc[0]


def _nan(x) -> bool:
  return x is None or (isinstance(x, float) and math.isnan(x))


def _ib(points, build: str | None = None) -> pd.DataFrame:
  return _run(
    [
      _IB_SCHEMA,
      *_header(build=build),
      *_samples("host_ib", "mlx5_0.1", points),
    ]
  )


def test_bridgeable_gap_spans_delta_and_arc_over_real_dt():
  out = _ib([(100.0, "1000"), (110.0, "-3"), (120.0, "1300")])
  gap = _row(out, 110.0)
  assert gap["value"] == -3
  assert _nan(gap["delta"]) and _nan(gap["arc"])
  nxt = _row(out, 120.0)
  assert nxt["delta"] == 300
  assert nxt["arc"] == pytest.approx(15.0)


def test_chain_breaking_gap_gives_nan_delta():
  out = _ib([(100.0, "1000"), (110.0, "-2"), (120.0, "1300"), (130.0, "1400")])
  assert _row(out, 110.0)["value"] == -2
  assert _nan(_row(out, 120.0)["delta"])
  assert _row(out, 130.0)["delta"] == 100


def test_bridge_limit_three_missed_bridge_four_break():
  three = _ib(
    [(100.0, "0"), (110.0, "-3"), (120.0, "-3"), (130.0, "-3"), (140.0, "40")]
  )
  assert _row(three, 140.0)["delta"] == 40
  assert _row(three, 140.0)["arc"] == pytest.approx(1.0)
  four = _ib(
    [
      (100.0, "0"),
      (110.0, "-3"),
      (120.0, "-3"),
      (130.0, "-3"),
      (140.0, "-3"),
      (150.0, "50"),
    ]
  )
  assert _nan(_row(four, 150.0)["delta"])


def test_one_missed_slow_tier_sample_bridges():
  out = _ib([(0.0, "100"), (600.0, "-1"), (1200.0, "700")])
  assert _row(out, 1200.0)["delta"] == 600
  assert _row(out, 1200.0)["arc"] == pytest.approx(0.5)


def test_restart_header_inside_gap_breaks_chain():
  lines = [_IB_SCHEMA, *_header()]
  lines += _samples("host_ib", "mlx5_0.1", [(100.0, "1000"), (110.0, "-3")])
  lines += _header()
  lines += _samples("host_ib", "mlx5_0.1", [(120.0, "1300")])
  out = _run(lines)
  assert _nan(_row(out, 120.0)["delta"])


_RAPL_SCHEMA = "!intel_x86_rapl pkg_energy,E,W=32,U=mJ"


def _pkg(points) -> list[str]:
  return _samples("intel_x86_rapl", "0", points)


def test_counter_drop_across_restart_header_breaks_chain():
  # Read as a W=32 wrap this was one 294971296 mJ delta.
  lines = [_RAPL_SCHEMA, *_header(), *_pkg([(100.0, "4000001000")])]
  lines += [*_header(), "$uptime 5"]
  lines += _pkg([(110.0, "5000"), (120.0, "6000")])
  out = _run(lines)
  assert _nan(_row(out, 110.0)["delta"])
  assert _row(out, 120.0)["delta"] == 1000


def test_counter_drop_across_restart_header_breaks_chain_through_carry():
  carry = stp.DeltaCarryState()
  _run([_RAPL_SCHEMA, *_header(), *_pkg([(100.0, "4000001000")])], carry)
  second = [_RAPL_SCHEMA, *_header(), "$uptime 5", *_pkg([(110.0, "5000")])]
  assert _nan(_row(_run(second, carry), 110.0)["delta"])


def test_w32_wrap_without_restart_header_still_unwraps():
  top = str(2**32 - 296)
  out = _run([_RAPL_SCHEMA, *_header(), *_pkg([(100.0, top), (110.0, "704")])])
  assert _row(out, 110.0)["delta"] == 1000


def test_w48_wrap_across_bridge():
  top = str(2**48 - 100)
  out = _run(
    [
      _HW_SCHEMA,
      *_header(build="aarch64-ver3.1-hw-arm-full"),
      *_samples(
        "host_cpu_hw", "0", [(100.0, top), (110.0, "-3"), (120.0, "50")]
      ),
    ]
  )
  assert _row(out, 120.0)["delta"] == 150


# Real monitor 3.1 x86 slug: direct perf readers, ``x86perf``, no ``likwid``.
_X86PERF_31_SLUG = (
  "x86_64-ver3.1-hw-ib-nvgpu-intelgpu-lustre-beegfs-x86perf-sent1-slowtier1"
)
# Monitor 3.0 x86 slug (LIKWID): still seen on old hosts and archived data.
_LIKWID_30_SLUG = "x86_64-ver3.0-hw-ib-likwid-full"


@pytest.mark.parametrize("code", ["-1", "-3", "-7", "-8", "-9"])
@pytest.mark.parametrize(
  "build", [_LIKWID_30_SLUG, None], ids=["likwid30", "nobuild"]
)
def test_history_likwid30_host_cpu_hw_bridgeable_codes_break_chain(code, build):
  """3.0 history: a LIKWID slug (or no ``$build``) leaves the
  ``host_cpu_hw`` bridge unproven, so a bridgeable gap breaks the chain."""
  out = _run(
    [
      _HW_SCHEMA,
      *_header(build=build),
      *_samples(
        "host_cpu_hw", "0", [(100.0, "10"), (110.0, code), (120.0, "30")]
      ),
    ]
  )
  assert _nan(_row(out, 120.0)["delta"])


@pytest.mark.parametrize("code", ["-1", "-3", "-7", "-8", "-9"])
@pytest.mark.parametrize(
  "build",
  [
    _X86PERF_31_SLUG,
    "x86_64-ver3.1-hw-ib-likwid-x86perf-full",
    "aarch64-ver3.1-hw-arm-full",
  ],
  ids=["x86perf31", "likwid_and_x86perf", "arm"],
)
def test_proven_host_cpu_hw_builds_bridge(code, build):
  """3.1 x86perf (and ARM) prove the bridge: a bridgeable gap is spanned,
  not downgraded to a chain break; ``x86perf`` wins over ``likwid``."""
  out = _run(
    [
      _HW_SCHEMA,
      *_header(build=build),
      *_samples(
        "host_cpu_hw", "0", [(100.0, "10"), (110.0, code), (120.0, "30")]
      ),
    ]
  )
  assert _row(out, 120.0)["delta"] == 20


def test_x86perf31_bridge_arc_over_real_dt():
  """3.1 x86perf ``host_cpu_hw`` bridged arc spans the real 20 s gap."""
  out = _run(
    [
      _HW_SCHEMA,
      *_header(build=_X86PERF_31_SLUG),
      *_samples(
        "host_cpu_hw", "0", [(100.0, "10"), (110.0, "-3"), (120.0, "50")]
      ),
    ]
  )
  row = _row(out, 120.0)
  assert row["delta"] == 40
  assert row["arc"] == pytest.approx(2.0)


def test_history_likwid30_override_leaves_other_types_bridging():
  """3.0 history: the LIKWID override is scoped to ``host_cpu_hw``."""
  out = _ib(
    [(100.0, "1000"), (110.0, "-3"), (120.0, "1300")],
    build=_LIKWID_30_SLUG,
  )
  assert _row(out, 120.0)["delta"] == 300


def test_unknown_code_is_kept_and_breaks_chain():
  out = _ib([(100.0, "1000"), (110.0, "-77"), (120.0, "1300")])
  assert _row(out, 110.0)["value"] == -77
  assert _nan(_row(out, 120.0)["delta"])


def _cpu(points0, points1) -> pd.DataFrame:
  lines = [_CPU_SCHEMA, *_header()]
  for (t, a), (_t, b) in zip(points0, points1, strict=True):
    lines += [f"{t} J1 hostA", f"cpu 0 {a}", f"cpu 1 {b}"]
  return _run(lines)


def test_collapse_sums_valid_devices_only():
  out = _cpu([(100.0, "100"), (110.0, "110")], [(100.0, "100"), (110.0, "-3")])
  assert _row(out, 110.0)["value"] == 110


def test_collapse_all_sentinel_devices_keep_modal_code():
  out = _cpu(
    [(100.0, "100"), (110.0, "-2")],
    [(100.0, "100"), (110.0, "-2")],
  )
  row = _row(out, 110.0)
  assert row["value"] == -2
  assert _nan(row["delta"]) and _nan(row["arc"])


def test_collapse_modal_code_tie_goes_to_lowest_magnitude():
  out = _cpu(
    [(100.0, "100"), (110.0, "-5")],
    [(100.0, "100"), (110.0, "-3")],
  )
  assert _row(out, 110.0)["value"] == -3


def test_collapsed_arc_sums_per_device_rates_when_one_device_bridges():
  out = _cpu(
    [(100.0, "100"), (110.0, "110"), (120.0, "120")],
    [(100.0, "100"), (110.0, "-3"), (120.0, "140")],
  )
  row = _row(out, 120.0)
  assert row["delta"] == 50
  # dev0: 10 over 10 s; dev1: 40 bridged over 20 s.
  assert row["arc"] == pytest.approx(1.0 + 2.0)


def test_collapsed_arc_unchanged_without_sentinels():
  out = _cpu(
    [(100.0, "100"), (110.0, "113")],
    [(100.0, "100"), (110.0, "107")],
  )
  assert _row(out, 110.0)["arc"] == 20 / 10


def test_bridge_spans_flush_boundary_through_carry():
  carry = stp.DeltaCarryState()
  first = [_IB_SCHEMA, *_header()]
  first += _samples("host_ib", "mlx5_0.1", [(100.0, "1000"), (110.0, "-3")])
  _run(first, carry=carry)
  second = [_IB_SCHEMA]
  second += _samples("host_ib", "mlx5_0.1", [(120.0, "1300")])
  out = _run(second, carry=carry)
  assert _row(out, 120.0)["delta"] == 300
  assert _row(out, 120.0)["arc"] == pytest.approx(15.0)


def test_bridge_limit_counts_sentinels_across_flushes():
  carry = stp.DeltaCarryState()
  first = [_IB_SCHEMA, *_header()]
  first += _samples(
    "host_ib", "mlx5_0.1", [(100.0, "0"), (110.0, "-3"), (120.0, "-3")]
  )
  _run(first, carry=carry)
  second = [_IB_SCHEMA]
  second += _samples("host_ib", "mlx5_0.1", [(130.0, "-3"), (140.0, "-3")])
  second += _samples("host_ib", "mlx5_0.1", [(150.0, "50")])
  out = _run(second, carry=carry)
  assert _nan(_row(out, 150.0)["delta"])


def test_chain_break_code_carried_across_flush():
  carry = stp.DeltaCarryState()
  first = [_IB_SCHEMA, *_header()]
  first += _samples("host_ib", "mlx5_0.1", [(100.0, "1000"), (110.0, "-5")])
  _run(first, carry=carry)
  second = [_IB_SCHEMA]
  second += _samples("host_ib", "mlx5_0.1", [(120.0, "-3"), (130.0, "1300")])
  out = _run(second, carry=carry)
  assert _nan(_row(out, 130.0)["delta"])


def test_no_negative_delta_or_arc_on_sentinel_corpus():
  out = _cpu(
    [(100.0, "100"), (110.0, "-3"), (120.0, "-2"), (130.0, "150")],
    [(100.0, "100"), (110.0, "105"), (120.0, "-1"), (130.0, "-4")],
  )
  valid = out[out["value"] >= 0]
  assert not (valid["delta"].dropna() < 0).any()
  assert not (valid["arc"].dropna() < 0).any()


def test_all_sentinel_or_event_restores_code_not_zero():
  """NVIDIA OR-class events reduce an all-missing group to 0.0; the restored
  sentinel row must replace that row, not sit beside it."""
  lines = ["!nvidia_gpu clocks_event_reasons,W=64,U=1", *_header()]
  lines += [
    "100.0 J1 hostA",
    "nvidia_gpu 0 4",
    "110.0 J1 hostA",
    "nvidia_gpu 0 -8",
  ]
  out = _run(lines)
  at = out[out["time"] == 110.0]
  assert len(at) == 1
  assert at.iloc[0]["value"] == -8
