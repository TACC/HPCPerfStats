"""grace_scf_verify: parsers and verdicts used by bin/verify_grace_scf_imc.sh on Grace nodes.

The SCF write unit is the open question (LIKWID's MEM group text treats CMEM_WR_TOTAL_BYTES as
bytes in one formula and x32 in another), so the unit verdict must flag a 32x error, not hide it.
"""

from __future__ import annotations

from lib import grace_scf_verify as g

PERFCTR = """\
--------------------------------------------------------------------------------
+-----------------------+---------+--------------+
|         Event         | Counter |  HWThread 0  |
+-----------------------+---------+--------------+
|      CMEM_RD_DATA     |   SCF0  | 6.250000e+07 |
|  CMEM_WR_TOTAL_BYTES  |   SCF1  |    123456    |
+-----------------------+---------+--------------+
"""
BENCH = "Cycles:\t\t1\nData volume (Byte):\t2000000000\nMByte/s:\t\t1000.00\n"
SCHEMA = ("$hpcperfstats 3.0\n$hostname c122-018\n"
          "!arm_aarch64_imc dram_cas_reads,E,W=48 dram_cas_writes,E,W=48\n"
          "!host_cpu_hw cpu_util_user_accum_us,E arm_dram_bw_bytes,E\n")


def payload(r0, w0, r1, w1):
    return ("1793700000.0 - c122-018\n"
            f"arm_aarch64_imc scf0 @full {r0} {w0}\narm_aarch64_imc scf1 @full {r1} {w1}\n"
            "host_cpu_hw 0 @full 5 6\nunknown_type x 1 2\n")


def test_perfctr_value_reads_table_cells():
    assert g.perfctr_value(PERFCTR, "CMEM_RD_DATA", "SCF0") == 6.25e7
    assert g.perfctr_value(PERFCTR, "CMEM_WR_TOTAL_BYTES", "SCF1") == 123456.0
    assert g.perfctr_value(PERFCTR, "CMEM_RD_DATA", "SCF1") is None
    assert g.perfctr_value("", "CMEM_RD_DATA", "SCF0") is None


def test_bench_volume():
    assert g.bench_volume_bytes(BENCH) == 2_000_000_000
    assert g.bench_volume_bytes("no volume here") is None


def test_unit_check_accepts_right_unit_and_flags_32x():
    # 2 GB read as 32-byte beats: 62.5 M beats (+ idle) -> 32 B/unit.
    ok = g.unit_check(2_000_000_000, 6.25e7 + 1000, 1000, 32.0)
    assert ok["ok"] and abs(ok["bytes_per_unit"] - 32.0) < 1e-6
    # Writes counted as 32-byte beats while assumed bytes: measured 32 B/unit vs expected 1.
    bad = g.unit_check(2_000_000_000, 6.25e7, 0, 1.0)
    assert not bad["ok"] and abs(bad["bytes_per_unit"] - 32.0) < 1e-6
    assert not g.unit_check(0, 10, 0, 32.0)["ok"]
    assert not g.unit_check(100, 5, 10, 32.0)["ok"]  # idle above load: no signal
    # Idle is scaled to the bench window: 1000 idle units over 2 s -> 2500 over a 5 s bench.
    scaled = g.unit_check(2_000_000_000, 6.25e7 + 2500, 1000, 32.0, bench_secs=5.0, idle_secs=2.0)
    assert abs(scaled["bytes_per_unit"] - 32.0) < 1e-6


def test_parse_payload_maps_keys_by_schema():
    schema = g.parse_schema(SCHEMA)
    assert schema["arm_aarch64_imc"] == ["dram_cas_reads", "dram_cas_writes"]
    rows = g.parse_payload(payload(10, 20, 30, 40), schema)
    assert rows[("arm_aarch64_imc", "scf0")] == {"dram_cas_reads": 10, "dram_cas_writes": 20}
    assert rows[("arm_aarch64_imc", "scf1")]["dram_cas_writes"] == 40
    assert ("unknown_type", "x") not in rows
    untiered = g.parse_payload("h\narm_aarch64_imc scf0 7 8\n", schema)
    assert untiered[("arm_aarch64_imc", "scf0")] == {"dram_cas_reads": 7, "dram_cas_writes": 8}


def test_daemon_delta_check_per_socket():
    schema = g.parse_schema(SCHEMA)
    before = g.parse_payload(payload(1000, 0, 500, 0), schema)
    # 2 GB read on socket 0 = 31.25 M 64-byte CAS; socket 1 nearly idle.
    after = g.parse_payload(payload(1000 + 31_250_000, 0, 600, 0), schema)
    res = g.daemon_delta_check(before, after, "scf0", "dram_cas_reads", 2_000_000_000, "scf1")
    assert res["ok"] and res["ratio"] == 1.0 and res["other_socket_ratio"] < 0.01
    # Reads emitted as raw 32-byte beats (missing /2) and a 32x write-unit error both fail.
    for factor in (2, 32):
        off = g.parse_payload(payload(1000 + 31_250_000 * factor, 0, 600, 0), schema)
        assert not g.daemon_delta_check(before, off, "scf0", "dram_cas_reads", 2_000_000_000, "scf1")["ok"]
    # Load attributed to the wrong socket fails.
    swapped = g.parse_payload(payload(1000, 0, 500 + 31_250_000, 0), schema)
    assert not g.daemon_delta_check(before, swapped, "scf0", "dram_cas_reads", 2_000_000_000, "scf1")["ok"]
    assert not g.daemon_delta_check({}, after, "scf0", "dram_cas_reads", 1, None)["ok"]


UTIL_SCHEMA = ("!host_cpu user,E,U=cs nice,E,U=cs system,E,U=cs\n"
               "!host_cpu_hw cpu_util_total_accum_us,E cpu_util_user_accum_us,E\n")


def util_payload(jiffies, hw_us):
    rows = [f"host_cpu {c} @full {j} 0 0" for c, j in jiffies.items()]
    rows += [f"host_cpu_hw {c} @full 0 {u}" for c, u in hw_us.items()]
    return "1 - h\n" + "\n".join(rows) + "\n"


def test_util_check_dcgm_matches_jiffies_without_lag():
    """Round-1 gate D1/G13: with DCGM refreshing at sample_freq, host_cpu_hw user util must still
    match /proc/stat user time on loaded CPUs, and must not trail it by a whole sample."""
    sch = g.parse_schema(UTIL_SCHEMA)
    cpus = ["8", "9"]
    before = g.parse_payload(util_payload({"8": 100, "9": 100}, {"8": 1_000_000, "9": 1_000_000}), sch)
    # 30 s at 100% user on each CPU: +3000 cs = +30 s = +30e6 us.
    mid = g.parse_payload(util_payload({"8": 1600, "9": 1600}, {"8": 16_000_000, "9": 16_000_000}), sch)
    after = g.parse_payload(util_payload({"8": 3100, "9": 3100}, {"8": 31_000_000, "9": 31_000_000}), sch)
    res = g.util_check(before, mid, after, cpus)
    assert res["ok"] and res["total_ratio"] == 1.0 and res["mid_ratio"] == 1.0
    # DCGM one sample (5 s) behind at the mid snapshot: mid ratio 10/15 -> fails the lag gate.
    lag = g.parse_payload(util_payload({"8": 1600, "9": 1600}, {"8": 11_000_000, "9": 11_000_000}), sch)
    assert not g.util_check(before, lag, after, cpus)["ok"]
    # Total off by 20% fails.
    short = g.parse_payload(util_payload({"8": 3100, "9": 3100}, {"8": 25_000_000, "9": 25_000_000}), sch)
    assert not g.util_check(before, mid, short, cpus)["ok"]
    # No load seen (jiffies flat) and a missing CPU row both fail.
    assert not g.util_check(before, before, before, cpus)["ok"]
    assert not g.util_check(before, mid, after, ["8", "77"])["ok"]


def test_main_cli(tmp_path, capsys):
    (tmp_path / "perf").write_text(PERFCTR)
    (tmp_path / "idle").write_text(PERFCTR.replace("6.250000e+07", "0"))
    (tmp_path / "bench").write_text(BENCH)
    rc = g.main(["x", "unit", str(tmp_path / "perf"), str(tmp_path / "idle"), str(tmp_path / "bench"),
                 "CMEM_RD_DATA", "SCF0", "32", "2.0", "2.0"])
    assert rc == 0 and '"ok": true' in capsys.readouterr().out
    (tmp_path / "schema").write_text(SCHEMA)
    (tmp_path / "p").write_text(payload(1, 2, 3, 4))
    assert g.main(["x", "rows", str(tmp_path / "schema"), str(tmp_path / "p")]) == 0
    assert g.main(["x", "bogus"]) == 2
