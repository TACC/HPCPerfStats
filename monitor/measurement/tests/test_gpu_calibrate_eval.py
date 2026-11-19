"""gpu_calibrate_eval: per-SM-per-clock tensor rates from the calibrator's exact FLOPs and the DEBUG
monitor's own nvidia_gpu row (tensor_hmma_active / tensor_dmma_active in %, gpu_sm_clock in MHz).

per_sm_clock = FLOP/s / (activity x SMs x clock). Only samples whose whole interval lies inside the
timed window count; the GPU must be idle before and after; the two sizes must agree within 5
percent; a missing row, key or sentinel is NOT_RUN, never a number.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lib import gpu_calibrate_eval as ev

SCHEMA = (
    "$hpcperfstats 3.1\n$hostname c122-018\n"
    "!nvidia_gpu gpu_util gpu_sm_clock,U=MHz tensor_hmma_active tensor_dmma_active\n"
    "!host_cpu user system\n"
)
SRC = Path(__file__).resolve().parents[2] / "src"
SMS = 148
CLOCK = 1800


def fast(ts, hmma, dmma, clock=CLOCK, dev="0"):
    return (f"{ts:.3f} - c122-018\n"
            f"nvidia_gpu {dev} 99 {clock} {hmma} {dmma}\n"
            "host_cpu 0 5 6\n")


def load_line(kind, n, t0, t1, per_clock, act_pct, calls=None, flops=None, cc="10.0"):
    """Calibrator output whose FLOP rate gives per_clock at act_pct activity, SMS and CLOCK."""
    secs = t1 - t0
    per_call = 2 * n ** 3
    if calls is None:
        rate = per_clock * act_pct / 100.0 * SMS * CLOCK * 1e6
        calls = max(1, round(rate * secs / per_call))
    flops = calls * per_call if flops is None else flops
    return ("gpu_tensor_calibrate warmup_calls=3\n"
            f"gpu_tensor_calibrate kind={kind} n={n} device=0 pci=0000:01:00.0 name=NVIDIA_GB200 "
            f"cc={cc} sms={SMS} calls={calls} flops={flops} elapsed_s={secs:.6f} "
            f"flops_per_s={flops / secs:.6e} start_epoch={t0:.6f} end_epoch={t1:.6f}\n")


def write_run(tmp_path, loads, dev="0"):
    """loads: [(kind, n, per_clock, active_pct)]; one sample per second, 10 s per load, 5 s gaps."""
    (tmp_path / "samples").mkdir()
    (tmp_path / "schema.txt").write_text(SCHEMA)
    t = 1000.0
    seq = 0

    def sample(ts, hmma, dmma):
        nonlocal seq
        (tmp_path / "samples" / f"s{seq:05d}.txt").write_text(fast(ts, hmma, dmma, dev=dev))
        seq += 1

    for i, (kind, n, per_clock, act) in enumerate(loads):
        for k in range(5):
            sample(t + k, 0, 0)
        t0, t1 = t + 5.2, t + 15.2
        for k in range(6, 16):
            sample(t + k, act if kind == "hmma" else 0, act if kind == "dmma" else 0)
        (tmp_path / f"load_{i}_{kind}_{n}.txt").write_text(load_line(kind, n, t0, t1, per_clock, act))
        t += 16
    for k in range(5):
        sample(t + k, 0, 0)
    return tmp_path


GOOD = [("hmma", 8192, 8192, 90), ("hmma", 16384, 8100, 92),
        ("dmma", 4096, 256, 80), ("dmma", 8192, 252, 81)]


def test_formula_and_table_row(tmp_path):
    out = write_run(tmp_path, GOOD)
    lines, verdict = ev.evaluate_run(str(out), str(SRC), "0")
    text = "\n".join(lines)
    assert verdict == "OK", text
    assert "{10, 0, 128, {128, 256, 512, 8192, 256}}," in text
    assert verdict_line(lines) == "GPU_TENSOR_CALIBRATE_OK"


def verdict_line(lines):
    return lines[-1]


def test_per_sm_clock_formula_exact():
    samples = [(float(t), {"tensor_hmma_active": 50 if 1 <= t <= 9 else 0, "gpu_sm_clock": 2000})
               for t in range(-2, 14)]
    load = ev.parse_load(load_line("hmma", 1024, 0.5, 9.5, 4096, 50, calls=1000))
    r = ev.evaluate_load(load, samples, "hmma")
    assert r["status"] == "PASS", r
    want = 1000 * 2 * 1024 ** 3 / 9.0 / (0.5 * SMS * 2000e6)
    assert r["per_sm_clock"] == pytest.approx(want, rel=1e-9)
    # Intervals [1,2] .. [8,9] are wholly inside [0.5, 9.5]; the first may trail (DCGM) and is
    # not averaged.
    assert r["inside"] == 7


def test_first_inside_sample_is_not_averaged_and_unsteady_activity_fails():
    def samples(first, rest):
        return [(float(t), {"tensor_hmma_active": 0 if t < 1 or t > 9 else first if t == 2 else rest,
                            "gpu_sm_clock": 1800}) for t in range(-2, 14)]

    load = ev.parse_load(load_line("hmma", 1024, 0.5, 9.5, 4096, 90, calls=1000))
    r = ev.evaluate_load(load, samples(30, 90), "hmma")
    assert r["status"] == "PASS" and r["activity"] == 90, r
    s = samples(90, 90)
    s[8] = (s[8][0], dict(s[8][1], tensor_hmma_active=70))
    r = ev.evaluate_load(load, s, "hmma")
    assert r["status"] == "FAIL" and "not steady" in r["reason"], r


def test_sizes_disagree_beyond_five_percent_fails(tmp_path):
    loads = [("hmma", 8192, 8192, 90), ("hmma", 16384, 7600, 90),
             ("dmma", 4096, 256, 80), ("dmma", 8192, 256, 80)]
    lines, verdict = ev.evaluate_run(str(write_run(tmp_path, loads)), str(SRC), "0")
    assert verdict == "FAIL"
    assert any(ln.startswith("FAIL hmma") and "5" in ln for ln in lines), lines
    assert not any(ln.startswith("ROW") for ln in lines)


def test_within_five_percent_passes():
    a = {"status": "PASS", "per_sm_clock": 8000.0, "n": 1}
    b = {"status": "PASS", "per_sm_clock": 8390.0, "n": 2}
    assert ev.combine("hmma", [a, b])["status"] == "PASS"
    b["per_sm_clock"] = 8500.0
    assert ev.combine("hmma", [a, b])["status"] == "FAIL"


def test_idle_dmma_pipe_is_not_present_and_row_keeps_zero(tmp_path):
    loads = [("hmma", 8192, 8192, 90), ("hmma", 16384, 8192, 90),
             ("dmma", 4096, 256, 1), ("dmma", 8192, 256, 0)]
    lines, verdict = ev.evaluate_run(str(write_run(tmp_path, loads)), str(SRC), "0")
    text = "\n".join(lines)
    assert verdict == "OK", text
    assert "NOT_PRESENT dmma" in text
    assert "{10, 0, 128, {128, 256, 512, 8192, 0}}," in text


def test_idle_hmma_pipe_fails():
    a = {"status": "NOT_PRESENT", "reason": "idle", "n": 1}
    assert ev.combine("hmma", [a, dict(a, n=2)])["status"] == "FAIL"


def test_missing_rows_are_not_run(tmp_path):
    out = write_run(tmp_path, GOOD, dev="1")
    lines, verdict = ev.evaluate_run(str(out), str(SRC), "0")
    assert verdict == "NOT_RUN"
    assert any("no nvidia_gpu 0 row" in ln for ln in lines), lines


def test_sentinel_key_is_not_run():
    samples = [(float(t), {"tensor_dmma_active": -3, "gpu_sm_clock": 1800}) for t in range(-2, 14)]
    load = ev.parse_load(load_line("dmma", 1024, 0.5, 9.5, 256, 50, calls=10))
    r = ev.evaluate_load(load, samples, "dmma")
    assert r["status"] == "NOT_RUN"
    assert "sentinel -3" in r["reason"]


def test_too_few_samples_inside_is_not_run():
    samples = [(float(t), {"tensor_hmma_active": 90, "gpu_sm_clock": 1800}) for t in (0, 5, 10, 15)]
    load = ev.parse_load(load_line("hmma", 1024, 4.0, 11.0, 4096, 90, calls=10))
    r = ev.evaluate_load(load, samples, "hmma")
    assert r["status"] == "NOT_RUN"
    assert "--seconds" in r["reason"]


def test_busy_before_load_fails():
    samples = [(float(t), {"tensor_hmma_active": 40 if t < 1 else 90, "gpu_sm_clock": 1800})
               for t in range(-2, 14)]
    for i, (t, row) in enumerate(samples):
        if t > 10:
            samples[i] = (t, dict(row, tensor_hmma_active=0))
    load = ev.parse_load(load_line("hmma", 1024, 0.5, 10.0, 4096, 90, calls=10))
    r = ev.evaluate_load(load, samples, "hmma")
    assert r["status"] == "FAIL"
    assert "before" in r["reason"]


def test_inexact_flop_count_fails():
    samples = [(float(t), {"tensor_hmma_active": 90 if 1 <= t <= 9 else 0, "gpu_sm_clock": 1800})
               for t in range(-2, 14)]
    load = ev.parse_load(load_line("hmma", 1024, 0.5, 9.5, 4096, 90, calls=10, flops=12345))
    r = ev.evaluate_load(load, samples, "hmma")
    assert r["status"] == "FAIL"
    assert "2*n^3" in r["reason"]


def test_no_load_output_is_not_run(tmp_path):
    out = write_run(tmp_path, GOOD)
    (out / "load_0_hmma_8192.txt").write_text("gpu_tensor_calibrate error=cublasGemmEx status 13\n")
    lines, verdict = ev.evaluate_run(str(out), str(SRC), "0")
    assert verdict == "NOT_RUN"
    assert any("cublasGemmEx" in ln for ln in lines), lines


def test_table_value_snaps_to_power_of_two_within_five_percent():
    assert ev.table_value(7950.0) == 8192
    assert ev.table_value(6000.0) == 6000
    assert ev.table_value(255.2) == 256


def test_pipe_index_and_row_come_from_the_monitor_source():
    header = (SRC / "nvidia_gpu_estimate.h").read_text()
    assert ev.pipe_index(header, "hmma") == 3
    assert ev.pipe_index(header, "dmma") == 4
    lanes, flops = ev.peaks_row((SRC / "nvidia_gpu_estimate.c").read_text(), 12, 0)
    assert lanes == 128 and flops[:3] == [4, 256, 512]
    assert ev.peaks_row("static const int x;", 10, 0) is None
    assert ev.format_row(9, 0, 128, [128, 256, 512, 4096, 256]) == "{9, 0, 128, {128, 256, 512, 4096, 256}},"


def test_main_exit_codes(tmp_path, capsys):
    out = write_run(tmp_path, GOOD)
    assert ev.main(["prog", str(out), str(SRC), "0"]) == 0
    assert capsys.readouterr().out.rstrip().endswith("GPU_TENSOR_CALIBRATE_OK")
    assert ev.main(["prog"]) == 2
