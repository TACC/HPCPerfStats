"""Preflight guards added after the 2026-11-09 campaign made legs incomparable across nodes.

* c122-018: the harness auto-fit cut every leg to 1200 s while Vista ran 1800 s.
* c642-041: CoreSight PMU devices were unbound, so ``arm_aarch64_imc`` silently disabled itself.
"""

from __future__ import annotations

import pytest

from lib import orchestrator, schedule


def _parse(*extra: str):
    return orchestrator.build_parser().parse_args(["--rpm", "x.rpm", *extra])


def test_harness_refuses_to_shorten_legs_by_default():
    args = _parse("--max-hours", "12")
    groups = {f"g{i}": [f"t{i}"] for i in range(8)}
    with pytest.raises(schedule.ScheduleDoesNotFit) as err:
        orchestrator._schedule(args, groups, 1, None)
    assert "--allow-fit" in str(err.value)
    assert err.value.min_hours > 12


def test_allow_fit_restores_auto_fit_and_records_it():
    args = _parse("--max-hours", "12", "--allow-fit")
    plan = orchestrator._schedule(args, {f"g{i}": [f"t{i}"] for i in range(8)}, 1, None)
    assert plan["fit_steps"] and plan["leg_seconds"] < 1800


def test_fitting_schedule_keeps_leg_seconds_without_allow_fit():
    plan = orchestrator._schedule(_parse("--max-hours", "20", "--blocks", "3"),
                                  {"filesystem": ["host_cpu"], "ib_mad": ["host_ib"]}, 1, None)
    assert plan["fit_steps"] == [] and plan["leg_seconds"] == 1800


def _pmu_tree(root, bound: int, unbound: int):
    for i in range(bound + unbound):
        dev = root / f"arm-cs-arch-pmu.{i}.auto"
        dev.mkdir(parents=True)
        if i < bound:
            (dev / "driver").mkdir()
    return root


def test_unbound_cs_pmu_refused(tmp_path):
    root = _pmu_tree(tmp_path / "platform", bound=0, unbound=5)
    with pytest.raises(orchestrator.PreflightError) as err:
        orchestrator.check_cs_pmus(str(root), allow=False)
    assert "5 of 5" in str(err.value) and "--allow-unbound-pmu" in str(err.value)


def test_unbound_cs_pmu_allowed_is_flagged(tmp_path):
    root = _pmu_tree(tmp_path / "platform", bound=3, unbound=2)
    flags = orchestrator.check_cs_pmus(str(root), allow=True)
    assert flags["unbound_pmu"]["devices"] == 5
    assert flags["unbound_pmu"]["unbound"] == ["arm-cs-arch-pmu.3.auto", "arm-cs-arch-pmu.4.auto"]


def test_bound_or_absent_cs_pmus_pass(tmp_path):
    assert orchestrator.check_cs_pmus(str(_pmu_tree(tmp_path / "p", 10, 0)), allow=False) == {}
    assert orchestrator.check_cs_pmus(str(tmp_path / "missing"), allow=False) == {}


def test_daemon_binary_check_passes_unchanged_and_skips_unknown(tmp_path):
    binary = tmp_path / "hpcperfstatsd"
    binary.write_bytes(b"v1")
    sha = orchestrator.provenance.sha256_file(binary)
    orchestrator.check_daemon_binary(str(binary), sha, "b1-ALL-first", "before")
    orchestrator.check_daemon_binary(str(binary), None, "b1-ALL-first", "before")


def test_daemon_binary_check_refuses_swapped_or_missing_binary(tmp_path):
    binary = tmp_path / "hpcperfstatsd"
    binary.write_bytes(b"v1")
    sha = orchestrator.provenance.sha256_file(binary)
    binary.write_bytes(b"v2")
    with pytest.raises(orchestrator.PreflightError) as err:
        orchestrator.check_daemon_binary(str(binary), sha, "b3-CADENCE-60.0", "before")
    assert "changed before leg b3-CADENCE-60.0" in str(err.value)
    binary.unlink()
    with pytest.raises(orchestrator.PreflightError) as err:
        orchestrator.check_daemon_binary(str(binary), sha, "b3-CADENCE-60.0", "before")
    assert "missing" in str(err.value)


def test_pmu_sharers_records_systemctl_state(monkeypatch):
    calls = []

    def fake_run(cmd, timeout=20.0):
        calls.append(cmd)
        return {"ok": False, "rc": 3, "stdout": "inactive\n"}
    monkeypatch.setattr(orchestrator.provenance, "run", fake_run)
    assert orchestrator.provenance.pmu_sharers() == {"nvidia-powerd": "inactive"}
    assert calls == [["systemctl", "is-active", "nvidia-powerd"]]
    monkeypatch.setattr(orchestrator.provenance, "run",
                        lambda cmd, timeout=20.0: {"ok": False, "error": "not found"})
    assert orchestrator.provenance.pmu_sharers() == {"nvidia-powerd": None}


def test_profiler_binary_keeps_the_daemon_comm():
    """The kernel comm is the basename cut to 15 chars; `hpcperfstatsd-profiler` became
    `hpcperfstatsd-p`, so the sampler never found the PROFILE leg's daemon."""
    assert orchestrator.Path(orchestrator.PROFILER_BINARY).name[:15] == orchestrator.DAEMON_COMM


def test_perf_event_holders_counts_every_program_holding_counters(tmp_path):
    """c573-002 2026-11-24: rotation starts when events exceed free counters, so every holder
    (nvidia-powerd, a user's perf job, our daemon) must be on record, not just one service."""
    import os
    from lib import provenance
    for pid, comm, links in ((10, "nvidia-powerd", ["anon_inode:[perf_event]"] * 3 + ["/dev/null"]),
                             (11, "hpcperfstatsd", ["anon_inode:[perf_event]"] * 2),
                             (12, "bash", ["/dev/pts/0"])):
        fd = tmp_path / str(pid) / "fd"
        fd.mkdir(parents=True)
        (tmp_path / str(pid) / "comm").write_text(comm + "\n")
        for i, target in enumerate(links):
            os.symlink(target, fd / str(i))
    (tmp_path / "13").mkdir()  # vanished / no fd dir: skipped
    assert provenance.perf_event_holders(str(tmp_path)) == {"nvidia-powerd": 3, "hpcperfstatsd": 2}
