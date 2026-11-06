"""Governor leg sets and busy-node preflight (plan monitor-cpu-seconds-round2, Approach H).

Finding 18 (c122-018): the same daemon costs ~8 core-s/h under ``performance`` and ~109 under
``ondemand`` at the same ~620 wakeups/s, and the round-1 harness numbers were taken under
whatever governor the node happened to run. These tests pin the harness side of D13/D25:
governors resolved per driver, an unsupported one fails before any change, the original
governor comes back on every exit path, and a busy node is refused.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lib import cpufreq, orchestrator, schedule

from tests.test_orchestrator_fake_host import (
    IDLE_STAT,
    fake_cpufreq,
    fake_host,
    governors,
)

__all__ = ["fake_host"]  # pytest fixture re-exported from the fake-host module


@pytest.mark.parametrize(("driver", "dynamic"), [
    ("cppc_cpufreq", "ondemand"), ("acpi-cpufreq", "ondemand"), ("amd-pstate", "ondemand"),
    ("intel_pstate", "powersave"), ("amd-pstate-epp", "powersave")])
def test_dynamic_resolves_per_driver(driver, dynamic):
    assert cpufreq.resolve(["performance", "dynamic"], driver) == ["performance", dynamic]


def test_dynamic_unknown_driver_and_spec_parsing():
    with pytest.raises(cpufreq.GovernorError, match="no dynamic governor known"):
        cpufreq.resolve(["dynamic"], "weird_cpufreq")
    assert cpufreq.parse_spec(None) == ["performance", "dynamic"]
    assert cpufreq.parse_spec(" Performance , performance,schedutil") == ["performance", "schedutil"]
    assert cpufreq.parse_spec("none") == []
    with pytest.raises(cpufreq.GovernorError):
        cpufreq.parse_spec("none,performance")


def test_plan_governors_checks_every_cpu_offers_it(tmp_path):
    root = fake_cpufreq(tmp_path / "intel", driver="intel_pstate", governor="powersave",
                        available="performance powersave")
    assert cpufreq.plan_governors(None, root)["governors"] == ["performance", "powersave"]
    with pytest.raises(cpufreq.GovernorError, match="ondemand not offered by cpufreq driver intel_pstate"):
        cpufreq.plan_governors("performance,ondemand", root)
    with pytest.raises(cpufreq.GovernorError, match="--governors none"):
        cpufreq.plan_governors(None, tmp_path / "no_cpufreq")
    assert cpufreq.plan_governors("none", tmp_path / "no_cpufreq")["governors"] == []


def test_set_all_restore_and_restore_lines(tmp_path):
    root = fake_cpufreq(tmp_path / "c", governor="schedutil")
    saved = cpufreq.read_state(root)["governors"]
    assert cpufreq.set_all("ondemand", root) == [0, 1]
    assert governors(root) == ["ondemand", "ondemand"]
    assert cpufreq.restore(saved, root) == []
    assert governors(root) == ["schedutil", "schedutil"]
    lines = cpufreq.restore_lines(saved, root)
    assert lines == [f"for f in {root}/cpu0/cpufreq/scaling_governor "
                     f"{root}/cpu1/cpufreq/scaling_governor; do echo schedutil > \"$f\"; done"]


def test_cur_freq_summary(tmp_path):
    root = fake_cpufreq(tmp_path / "c", cpus=3)
    snap = cpufreq.sample_freq(cpufreq.cur_freq_files(root))
    assert snap == {"min": 81.0, "p50": 82.0, "max": 83.0, "n": 3}
    assert cpufreq.sample_freq([]) is None


def test_schedule_repeats_full_leg_set_per_governor():
    one = schedule.build_schedule(["filesystem", "likwid"], blocks=2, seed=4, max_hours=99)
    two = schedule.build_schedule(["filesystem", "likwid"], blocks=2, seed=4, max_hours=99,
                                  governors=["performance", "ondemand"], cadence_sweep=[60],
                                  profile_collectors=True)
    base = [(leg["kind"], leg["group"], leg["block"]) for leg in one["legs"]]
    perf = [leg for leg in two["legs"] if leg["governor"] == "performance"]
    ond = [leg for leg in two["legs"] if leg["governor"] == "ondemand"]
    assert [(lg["kind"], lg["group"], lg["block"]) for lg in ond] == base
    assert [(lg["kind"], lg["group"], lg["block"]) for lg in perf
            if lg["kind"] not in ("CADENCE", "PROFILE")] == base
    assert two["legs"].index(ond[0]) == len(perf)  # sets run back to back, performance first
    assert {lg["kind"] for lg in ond} & {"CADENCE", "PROFILE"} == set()  # diagnostics run once
    ids = [leg["leg_id"] for leg in two["legs"]]
    assert len(ids) == len(set(ids)) and ids[0] == "performance-b1-OFF-"
    assert two["governors"] == ["performance", "ondemand"]
    # No governor dimension: legs and ids exactly as before (old results stay comparable).
    assert all(leg["governor"] is None for leg in one["legs"]) and one["legs"][0]["leg_id"] == "b1-OFF-"


def _args(fake, *extra):
    return orchestrator.build_parser().parse_args([
        "--rpm", str(fake["rpm"]), "--out", str(fake["run"]), "--leg-seconds", "2",
        "--settle-seconds", "1", "--fwq-seconds", "1", "--blocks", "1", "--seed", "5",
        "--fwq-quantum-us", "200", "--force", "--rmq-mgmt-port", "1", "--fwq-cpus", "0", *extra])


def test_unsupported_governor_fails_before_any_leg_or_change(fake_host, monkeypatch):
    sysfs = fake_cpufreq(Path(fake_host["paths"]["SYSFS_CPU"]).parent / "intel",
                         driver="intel_pstate", governor="powersave",
                         available="performance powersave")
    monkeypatch.setattr(orchestrator, "SYSFS_CPU", str(sysfs))
    with pytest.raises(orchestrator.PreflightError, match="not offered by cpufreq driver intel_pstate"):
        orchestrator.Run(_args(fake_host, "--governors", "performance,ondemand")).execute()
    assert governors(sysfs) == ["powersave", "powersave"]
    log = (fake_host["run"] / "raw" / "commands.log")
    assert "rpm -Uvh" not in (log.read_text() if log.is_file() else "")
    assert not (fake_host["run"] / "raw" / "legs").exists()


def test_governor_restored_when_run_aborts_mid_schedule(fake_host, monkeypatch):
    """The sticky-type abort (PreflightError after the first ON leg) still restores governors."""
    monkeypatch.setenv("FAKE_STICKY", "host_mem")
    with pytest.raises(orchestrator.PreflightError, match="kept types"):
        orchestrator.Run(_args(fake_host)).execute()
    assert governors(fake_host["sysfs"]) == ["schedutil", "schedutil"]
    run_json = json.loads((fake_host["run"] / "raw" / "run.json").read_text())
    assert run_json["state"]["governor_changes"][0]["governor"] == "performance"
    assert run_json["state"]["cleanup"]["governor"] == "ok"


def test_governor_restored_on_signal(fake_host, monkeypatch):
    """SIGTERM/SIGHUP raise SystemExit (signal handler); the finally/cleanup path restores."""
    def boom(self, leg, plan):
        assert governors(fake_host["sysfs"]) == ["performance", "performance"]
        raise SystemExit(128 + 15)
    monkeypatch.setattr(orchestrator.Run, "run_leg", boom)
    with pytest.raises(SystemExit):
        orchestrator.Run(_args(fake_host)).execute()
    assert governors(fake_host["sysfs"]) == ["schedutil", "schedutil"]


def test_check_busy_node():
    assert orchestrator.check_busy_node(None, False) == {}
    assert orchestrator.check_busy_node(0.8, False) == {}
    with pytest.raises(orchestrator.PreflightError, match="--allow-busy-node"):
        orchestrator.check_busy_node(17.3, False)
    assert orchestrator.check_busy_node(17.3, True)["busy_node"]["busy_cores"] == 17.3


def test_busy_node_refused_at_preflight_unless_allowed(fake_host, monkeypatch):
    """Round-1 harness ran with ~17 cores busy in every leg, OFF included (finding 13)."""
    stat = Path(fake_host["paths"]["PROC_ROOT"]) / "stat"
    clk = orchestrator.CLK_TCK

    def busy_sleep(seconds):
        # 17 cores of user time over the probe window.
        stat.write_text(f"cpu  {100 + int(17 * clk * seconds)} 0 100 10000 0 0 0 0 0 0\n")
    monkeypatch.setattr(orchestrator, "BUSY_PROBE_SECONDS", 1.0)
    monkeypatch.setattr(orchestrator.Run, "main_pid", lambda self: None)  # daemon not running
    monkeypatch.setattr(orchestrator.time, "sleep", busy_sleep)
    monkeypatch.setattr(orchestrator.time, "time", iter(range(1000, 2000)).__next__)
    with pytest.raises(orchestrator.PreflightError, match="node busy: 17.00 cores"):
        orchestrator.Run(_args(fake_host)).execute()
    assert governors(fake_host["sysfs"]) == ["schedutil", "schedutil"]

    stat.write_text(IDLE_STAT)
    run = orchestrator.Run(_args(fake_host, "--allow-busy-node"))
    run.out = run.raw = fake_host["run"]
    run.raw.mkdir(parents=True, exist_ok=True)
    run.host, run.flags = orchestrator.Host(fake_host["run"] / "commands.log"), {}
    monkeypatch.setattr(orchestrator.time, "time", iter(range(3000, 4000)).__next__)
    run.preflight()
    assert run.flags["busy_node"]["busy_cores"] == pytest.approx(17.0)
