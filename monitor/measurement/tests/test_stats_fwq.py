"""Statistics helpers (percentiles, block bootstrap) and the FWQ parser."""

from __future__ import annotations

import random
import struct

from lib import fwq, stats


def test_percentile_linear_interpolation():
    assert stats.percentile([1, 2, 3, 4], 50) == 2.5
    assert stats.percentile([5], 99) == 5
    assert stats.percentile([], 50) is None
    assert stats.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 90) == 9.1


def _ar1(n: int, phi: float, mu: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    out, x = [], 0.0
    for _ in range(n):
        x = phi * x + rng.gauss(0.0, 1.0)
        out.append(mu + x)
    return out


def test_block_bootstrap_ci_deterministic():
    series = _ar1(2000, 0.8, 10.0, seed=7)
    a = stats.mean_ci(series, seed=1234)
    b = stats.mean_ci(series, seed=1234)
    assert a == b
    assert a["lo"] < 10.0 < a["hi"]
    assert a["n"] == 2000 and a["method"] == "moving_block_bootstrap"
    assert a["block"] >= 2
    iid = stats.mean_ci(series, seed=1234, block=1)
    assert (iid["hi"] - iid["lo"]) < (a["hi"] - a["lo"])


def test_mean_ci_small_and_empty():
    assert stats.mean_ci([], seed=1) is None
    one = stats.mean_ci([3.0], seed=1)
    assert one["mean"] == 3.0 and one["lo"] is None


def test_diff_ci_combines_half_widths():
    a = {"mean": 10.0, "lo": 9.0, "hi": 11.0}
    b = {"mean": 4.0, "lo": 3.0, "hi": 5.0}
    d = stats.diff_ci(a, b)
    assert d["mean"] == 6.0
    assert abs((d["hi"] - d["lo"]) / 2 - 2 ** 0.5) < 1e-12
    assert stats.diff_ci(a, None) is None
    assert stats.ci_overlap({"lo": 1, "hi": 2}, {"lo": 1.5, "hi": 3})
    assert not stats.ci_overlap({"lo": 1, "hi": 2}, {"lo": 2.5, "hi": 3})


def test_combine_replicates():
    reps = [{"mean": 2.0, "lo": 1.0, "hi": 3.0}, {"mean": 4.0, "lo": 3.0, "hi": 5.0}]
    c = stats.combine_replicates(reps)
    assert c["mean"] == 3.0 and c["n"] == 2
    # Half-width is the larger of the pooled within-leg half-width (sqrt(2)/2 here)
    # and the half-range between replicates (1.0 here), so replicate disagreement shows.
    assert c["lo"] == 2.0 and c["hi"] == 4.0
    tight = stats.combine_replicates([{"mean": 3.0, "lo": 1.0, "hi": 5.0},
                                      {"mean": 3.0, "lo": 1.0, "hi": 5.0}])
    assert abs(tight["hi"] - 3.0 - 2 * 2 ** 0.5 / 2) < 1e-12
    assert stats.combine_replicates([]) is None


def test_fwq_parser_and_slowdown(tmp_path):
    off = [1000] * 990 + [1100] * 10
    on = [1000] * 950 + [1500] * 50
    for name, vals in (("off", off), ("on", on)):
        d = tmp_path / name
        d.mkdir()
        (d / "fwq_cpu0.bin").write_bytes(struct.pack(f"<{len(vals)}Q", *vals))
        (d / "fwq_cpu1.bin").write_bytes(struct.pack(f"<{len(vals)}Q", *vals))
    off_q = fwq.load_quanta(tmp_path / "off")
    assert sorted(off_q) == [0, 1] and len(off_q[0]) == 1000
    off_s = fwq.summarize(off_q)
    on_s = fwq.summarize(fwq.load_quanta(tmp_path / "on"))
    assert off_s["n"] == 2000 and off_s["min_ns"] == 1000
    cmp = fwq.compare(on_s, off_s, fwq.load_quanta(tmp_path / "on"))
    assert abs(cmp["mean_slowdown_pct"] - ((0.95 * 1000 + 0.05 * 1500) / (0.99 * 1000 + 0.01 * 1100) - 1) * 100) < 1e-9
    assert cmp["frac_above_off_p99"] == 0.05
    assert on_s["max_detour_ns"] == 500
    assert on_s["p99_ns"] == 1500


def test_fwq_missing_dir_is_empty(tmp_path):
    assert fwq.load_quanta(tmp_path / "nope") == {}
    assert fwq.summarize({}) is None


def test_fwq_c_probe_builds_calibrates_and_runs(tmp_path):
    """Regression: calibration must hit the target quantum (was ~0.4x of 1000 us)."""
    import shutil
    import subprocess

    import pytest

    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    exe = fwq.build(tmp_path / "bin")
    work = int(subprocess.run([str(exe), "--calibrate", "500", "-c", "0"], capture_output=True, text=True,
                              check=True).stdout)
    out = tmp_path / "q"
    out.mkdir()
    subprocess.run([str(exe), "-c", "0", "-s", "1", "-w", str(work), "-o", str(out)], check=True)
    summary = fwq.summarize(fwq.load_quanta(out))
    assert summary["cpus"] == [0] and summary["n"] > 100
    assert 0.8 * 500_000 <= summary["min_ns"] <= 1.2 * 500_000, summary
    bad = subprocess.run([str(exe), "-c", "x", "-s", "1", "-w", "1", "-o", str(out)],
                         capture_output=True, text=True)
    assert bad.returncode == 2 and "bad CPU list" in bad.stderr
