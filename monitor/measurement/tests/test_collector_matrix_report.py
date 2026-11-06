"""Per-driver decomposition, burst cost model, profile.json schema, aggregate table."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest
from lib import collector_matrix, leg_summary, profile_report

ROOT = Path(__file__).resolve().parents[1]


def _ci(mean, hw=0.5, n=1800):
    return {"mean": mean, "lo": mean - hw, "hi": mean + hw, "n": n}


def _leg(kind, block, group=None, cpu=None, types=None, uss=10.0, broker=1000.0, status="ok",
         node_busy=0.01, missing=None, tag=""):
    proc = None
    if kind != "OFF" and cpu is not None:
        proc = {"cpu_core_s_per_h": _ci(cpu), "uss_mib": uss, "broker_bytes_acked_per_h": broker,
                "wakeups_per_s": 1.0, "perf_event_fds": 0, "init_cpu_s": 0.1,
                "ms_per_fast_sample": cpu / 120.0, "ms_per_full_sample": cpu / 6.0}
    return {"leg_id": f"b{block}-{kind}-{group or tag}", "kind": kind, "block": block,
            "group": group, "status": status, "verified_types": types or [],
            "missing_types": missing or [], "process": proc,
            "node": {"busy_cores": _ci(node_busy, 0.001)}}


GROUPS = {"filesystem": ["host_cpu", "host_mem"], "likwid": ["host_cpu_hw", "intel_x86_rapl"]}
ACTIVE = ["host_cpu", "host_cpu_hw", "host_mem", "intel_x86_rapl"]


def _legs(residual=0.0, none_ok=True):
    legs = []
    for b in (1, 2):
        legs.append(_leg("OFF", b))
        legs.append(_leg("NONE", b, cpu=2.0, types=[], status="ok" if none_ok else "failed"))
        legs.append(_leg("ALL", b, cpu=2.0 + 3.0 + 5.0 + residual, types=ACTIVE, tag="first"))
        legs.append(_leg("SOLO", b, "filesystem", cpu=2.0 + 3.0, types=GROUPS["filesystem"]))
        legs.append(_leg("SOLO", b, "likwid", cpu=2.0 + 5.0, types=GROUPS["likwid"]))
        legs.append(_leg("ALL", b, cpu=2.0 + 3.0 + 5.0 + residual, types=ACTIVE, tag="second"))
    legs.append(_leg("OFF", 3))
    return legs


def test_collector_matrix_additivity():
    m = collector_matrix.build_matrix(_legs(), GROUPS, ACTIVE)
    rows = {r["group"]: r for r in m["rows"]}
    assert abs(rows["likwid"]["cpu_core_s_per_h"]["mean"] - 5.0) < 1e-9
    assert abs(rows["filesystem"]["cpu_core_s_per_h"]["mean"] - 3.0) < 1e-9
    assert [r["group"] for r in m["rows"]] == ["likwid", "filesystem"]
    assert abs(rows["likwid"]["share_of_all"] - 0.5) < 1e-9
    assert rows["likwid"]["cpu_core_s_per_h"]["n_blocks"] == 2
    assert abs(m["framework"]["cpu_core_s_per_h"]["mean"] - 2.0) < 1e-9
    assert abs(m["residual"]["cpu_core_s_per_h"]["mean"]) < 1e-9
    assert m["residual"]["large"] is False
    big = collector_matrix.build_matrix(_legs(residual=4.0), GROUPS, ACTIVE)
    assert abs(big["residual"]["cpu_core_s_per_h"]["mean"] - 4.0) < 1e-9
    assert big["residual"]["large"] is True


def test_collector_matrix_none_failed_falls_back_to_off():
    m = collector_matrix.build_matrix(_legs(none_ok=False), GROUPS, ACTIVE)
    assert m["framework"]["status"] == "NOT_MEASURED"
    rows = {r["group"]: r for r in m["rows"]}
    assert abs(rows["likwid"]["cpu_core_s_per_h"]["mean"] - 7.0) < 1e-9
    assert "includes framework cost" in " ".join(rows["likwid"]["notes"])


def test_solo_leg_mismatch_partial_group():
    legs = _legs()
    for leg in legs:
        if leg["kind"] == "SOLO" and leg["group"] == "likwid":
            leg["verified_types"] = ["host_cpu_hw"]
            leg["missing_types"] = ["intel_x86_rapl"]
    rows = {r["group"]: r for r in collector_matrix.build_matrix(legs, GROUPS, ACTIVE)["rows"]}
    assert rows["likwid"]["types_measured"] == ["host_cpu_hw"]
    assert "intel_x86_rapl" in " ".join(rows["likwid"]["notes"])
    for leg in legs:
        if leg["kind"] == "SOLO" and leg["group"] == "likwid":
            leg["verified_types"] = []
            leg["missing_types"] = GROUPS["likwid"]
    rows = {r["group"]: r for r in collector_matrix.build_matrix(legs, GROUPS, ACTIVE)["rows"]}
    assert rows["likwid"]["status"] == "NOT_MEASURED"
    assert "init failed in isolation" in rows["likwid"]["reason"]


def test_type_mismatch_legs_name_their_status_not_init_failure():
    """Regression (Horizon c122-018): NONE/SOLO legs the daemon could not trim reported
    "init failed in isolation" and "the zero-collector daemon failed"; neither was true."""
    legs = _legs()
    for leg in legs:
        if leg["kind"] in ("NONE", "SOLO"):
            leg["status"] = "type_mismatch"
    m = collector_matrix.build_matrix(legs, GROUPS, ACTIVE)
    assert "type_mismatch" in m["framework"]["reason"]
    for row in m["rows"]:
        assert row["status"] == "NOT_MEASURED" and "type_mismatch" in row["reason"], row


def test_burst_cost_model():
    # 1 Hz CPU-seconds series: idle 0.001 s/s, fast sample every 30 s (+0.02 s),
    # full sample every 600 s (+0.2 s), send every 300 s offset 7 s (+0.05 s).
    t0 = 1_800_000_000
    series = []
    for i in range(1800):
        t = t0 + i
        cpu = 0.001
        if t % 600 == 0:
            cpu += 0.2
        elif t % 30 == 0:
            cpu += 0.02
        if (t - 7) % 300 == 0:
            cpu += 0.05
        series.append((float(t), cpu))
    model = leg_summary.burst_cost_model(series, sample_freq=30, sample_freq_slow=600,
                                         enable_slow_tier=1)
    assert abs(model["ms_per_fast_sample"] - 20.0) < 0.5
    assert abs(model["ms_per_full_sample"] - 200.0) < 0.5
    assert abs(model["ms_per_send"] - 50.0) < 0.5
    assert model["n_fast"] >= 50 and model["n_full"] == 3 and model["n_send"] == 6


def _write_run(tmp_path, legs, run_meta):
    run = tmp_path / "run"
    (run / "raw" / "legs").mkdir(parents=True)
    for leg in legs:
        d = run / "raw" / "legs" / leg["leg_id"]
        d.mkdir()
        (d / "summary.json").write_text(json.dumps(leg))
    (run / "raw" / "run.json").write_text(json.dumps(run_meta))
    (run / "raw" / "provenance.json").write_text(json.dumps({
        "host": {"hostname": "c1.example", "short": "c1", "arch": "x86_64",
                 "cpu_model": "Intel(R) Xeon(R) Platinum 8480+", "gpus": []}}))
    return run


RUN_META = {"schema": 1, "groups": GROUPS, "active_types": ACTIVE, "flags": {},
            "schedule": {"legs": [], "seed": 1, "fit_steps": []},
            "planned_leg_ids": []}


def test_profile_schema_v1_not_measured(tmp_path):
    run = _write_run(tmp_path, [], RUN_META)
    profile = profile_report.build_profile(run)
    assert profile["schema_version"] == profile_report.PROFILE_SCHEMA_VERSION == 1
    for key, _label, _unit in profile_report.HEADLINE_METRICS:
        slot = profile["metrics"][key]
        assert slot.get("status") == "NOT_MEASURED", key
        assert slot["reason"], key
        assert slot.get("value") != 0
    assert profile["metrics"]["gpu_interference"]["status"] == "NOT_MEASURED"


def test_profile_metrics_measured_values(tmp_path):
    run = _write_run(tmp_path, _legs(), RUN_META)
    profile = profile_report.build_profile(run)
    cpu = profile["metrics"]["daemon_cpu_core_s_per_h"]
    assert abs(cpu["value"] - 10.0) < 1e-9 and cpu["unit"] == "core-s/h"
    assert cpu["ci95"][0] < 10.0 < cpu["ci95"][1] and cpu["source"]
    day = profile["metrics"]["daemon_cpu_core_s_per_day"]
    assert abs(day["value"] - 240.0) < 1e-6
    outputs = profile_report.write_outputs(run)
    for name in ("profile.json", "report.md", "profile_row.csv"):
        assert (run / name).is_file(), name
    assert "likwid" in (run / "report.md").read_text()
    rows = list(csv.DictReader((run / "profile_row.csv").open()))
    assert len(rows) == 1 and float(rows[0]["daemon_cpu_core_s_per_h"]) == 10.0
    assert outputs["profile"] == run / "profile.json"


def test_baseline_drift_flag(tmp_path):
    legs = _legs()
    legs[-1]["node"]["busy_cores"] = _ci(0.5, 0.001)
    run = _write_run(tmp_path, legs, RUN_META)
    profile = profile_report.build_profile(run)
    assert profile["baseline_drift"]["OFF"] is True
    assert profile["baseline_drift"]["ALL"] is False


def test_partial_report_after_each_leg(tmp_path):
    meta = dict(RUN_META)
    meta["planned_leg_ids"] = ["b1-OFF-", "b1-NONE-", "b1-ALL-", "b1-SOLO-likwid"]
    legs = [_leg("OFF", 1), _leg("NONE", 1, cpu=2.0, types=[]),
            _leg("ALL", 1, cpu=10.0, types=ACTIVE)]
    run = _write_run(tmp_path, legs, meta)
    profile = profile_report.build_profile(run)
    assert profile["run_complete"] is False
    assert profile["legs_missing"] == ["b1-SOLO-likwid"]
    rows = {r["group"]: r for r in profile["collector_matrix"]["rows"]}
    assert rows["likwid"]["status"] == "NOT_MEASURED"
    assert rows["likwid"]["reason"] == "run interrupted"
    assert profile["metrics"]["daemon_cpu_core_s_per_h"]["value"] == 10.0


def test_aggregate_profiles_latex(tmp_path):
    paths = []
    for i, (host, flags) in enumerate((("c1", {}), ("c2", {}), ("dev", {"not_idle": "smoke only"}))):
        meta = dict(RUN_META)
        meta["flags"] = flags
        run = _write_run(tmp_path / f"r{i}", _legs(), meta)
        prov = json.loads((run / "raw" / "provenance.json").read_text())
        prov["host"]["short"] = host
        prov["host"]["cpu_model"] = "AMD EPYC 9654 96-Core & co_1"
        (run / "raw" / "provenance.json").write_text(json.dumps(prov))
        profile_report.write_outputs(run)
        paths.append(str(run / "profile.json"))
    out = tmp_path / "agg"
    res = subprocess.run([sys.executable, str(ROOT / "bin" / "aggregate_profiles.py"), "--out", str(out),
                          *paths], capture_output=True, text=True, check=True)
    assert "excluded" in res.stderr and "dev" in res.stderr
    rows = list(csv.DictReader((out / "profiles.csv").open()))
    assert [r["host"] for r in rows] == ["c1", "c2"]
    md = (out / "profiles.md").read_text()
    assert md.count("\n| c") == 2
    tex = (out / "profiles.tex").read_text()
    assert "\\toprule" in tex and "\\bottomrule" in tex
    assert "9654 96-Core \\& co\\_1" in tex
    assert tex.count("\\\\\n") >= 3


def test_contaminated_legs_excluded_unless_smoke_run(tmp_path):
    legs = _legs()
    for leg in legs:
        leg["contaminated"] = True
    run = _write_run(tmp_path / "idle", legs, RUN_META)
    profile = profile_report.build_profile(run)
    assert profile["metrics"]["daemon_cpu_core_s_per_h"]["status"] == "NOT_MEASURED"
    assert all(r["status"] == "NOT_MEASURED" for r in profile["collector_matrix"]["rows"])
    smoke = dict(RUN_META)
    smoke["flags"] = {"not_idle": "smoke only"}
    run = _write_run(tmp_path / "smoke", legs, smoke)
    profile = profile_report.build_profile(run)
    assert profile["not_idle"] == "smoke only"
    assert abs(profile["metrics"]["daemon_cpu_core_s_per_h"]["value"] - 10.0) < 1e-9


def test_collector_profile_section(tmp_path):
    run = _write_run(tmp_path / "none", _legs(), RUN_META)
    prof = profile_report.build_profile(run)["collector_profile"]
    assert prof == {"status": "NOT_MEASURED", "reason": "--profile-collectors not requested"}
    legs = _legs() + [_leg("PROFILE", 2, cpu=11.0, types=ACTIVE)]
    run = _write_run(tmp_path / "prof", legs, RUN_META)
    jdir = run / "raw" / "legs" / legs[-1]["leg_id"]
    (jdir / "journal.txt").write_text((ROOT / "fixtures" / "journal_metric_profiler.txt").read_text())
    profile = profile_report.build_profile(run)
    cp = profile["collector_profile"]
    assert cp["status"] == "ok" and "not production" in cp["label"]
    assert cp["cycles"] == 134 and list(cp["types"])[0] == "host_cpu"
    assert "filesystem" in cp["groups"]
    assert abs(profile["metrics"]["daemon_cpu_core_s_per_h"]["value"] - 10.0) < 1e-9  # PROFILE excluded
    assert "Instrumented collector profile" in profile_report._md(profile)
    (jdir / "journal.txt").write_text("Enabled types: host_cpu\n")
    cp = profile_report.build_profile(run)["collector_profile"]
    assert cp["status"] == "NOT_MEASURED" and "no metric-profiler report" in cp["reason"]


def test_report_per_governor_sets_and_old_results_unnamed(tmp_path):
    """D13: each governor's legs give their own metrics and per-driver matrix (an ondemand ALL
    leg never pairs with a performance NONE leg); wakeups/s is the headline next to core-s/h.
    Results without the governor dimension render as one unnamed set, as before."""
    legs = []
    for gov, scale in (("performance", 1.0), ("ondemand", 13.0)):
        for leg in _legs():
            leg = json.loads(json.dumps(leg))
            leg["governor"], leg["leg_id"] = gov, f"{gov}-{leg['leg_id']}"
            if leg["process"]:
                leg["process"]["cpu_core_s_per_h"] = _ci(leg["process"]["cpu_core_s_per_h"]["mean"] * scale)
                leg["process"]["wakeups_per_s"] = 620.0
                leg["process"]["wakeups_scope"] = "all_threads"
            legs.append(leg)
    meta = dict(RUN_META, schedule={"governors": ["performance", "ondemand"]})
    run = _write_run(tmp_path / "gov", legs, meta)
    profile_report.write_outputs(run)
    profile = json.loads((run / "profile.json").read_text())
    sets = {gs["governor"]: gs for gs in profile["governor_sets"]}
    assert list(sets) == ["performance", "ondemand"]
    assert sets["performance"]["headline"]["cpu_core_s_per_h"]["value"] == pytest.approx(10.0)
    assert sets["ondemand"]["headline"]["cpu_core_s_per_h"]["value"] == pytest.approx(130.0)
    assert sets["ondemand"]["headline"]["wakeups_per_s"]["value"] == pytest.approx(620.0)
    rows = {r["group"]: r for r in sets["ondemand"]["collector_matrix"]["rows"]}
    assert rows["likwid"]["cpu_core_s_per_h"]["mean"] == pytest.approx(65.0)  # 13 x 5, same set only
    assert profile["metrics_governor"] == "performance"
    assert profile["metrics"]["daemon_cpu_core_s_per_h"]["value"] == pytest.approx(10.0)
    md = (run / "report.md").read_text()
    assert "## Headline: daemon wakeups/s and CPU per governor" in md
    assert "| ondemand | 620 [620, 620] /s | 130 [" in md
    assert "## Per-driver cost (SOLO − NONE, paired within block) — governor `ondemand`" in md

    old = _write_run(tmp_path / "old", _legs(), RUN_META)
    profile_report.write_outputs(old)
    oprof = json.loads((old / "profile.json").read_text())
    assert [gs["governor"] for gs in oprof["governor_sets"]] == [None]
    omd = (old / "report.md").read_text()
    assert "## Headline metrics\n" in omd and "— governor" not in omd
