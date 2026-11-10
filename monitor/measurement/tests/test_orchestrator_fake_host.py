"""End-to-end ``Run.execute`` on a fake host (no root): stub systemctl/journalctl/rpm,
a fake ``hpcperfstatsd`` process, system paths redirected under tmp, very short legs.

Proves the real-run control flow: install over a site conf (the stub RPM overwrites it the
way the real spec does), discovery from the journal, drop-ins per leg, per-leg verification,
summaries after each leg, and the restore path (drop-in gone, conf restored, queue cleanup
recorded, every CPU's governor restored) — everything except the privileged commands
themselves. cpufreq sysfs and /proc/stat are fake trees (``SYSFS_CPU`` / ``PROC_ROOT``).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import stat
from pathlib import Path

import pytest
from lib import orchestrator

ALL_TYPES = "host_cpu,host_mem,host_cpu_hw,intel_x86_rapl"

SYSTEMCTL = r"""#!/usr/bin/env bash
S="${FAKE_STATE:?}"
alive() { test -f "$S/pid" && kill -0 "$(cat "$S/pid")" 2>/dev/null; }
stop() { if alive; then kill "$(cat "$S/pid")"; fi; rm -f "$S/pid"; }
case "$1" in
  show)
    if test "$2" = "-p"; then alive && cat "$S/pid" || echo 0
    else printf 'ExecStart={ path=/usr/sbin/hpcperfstatsd }\nNice=10\nIOSchedulingClass=idle\n'; fi ;;
  is-active) alive ;;
  is-enabled) exit 1 ;;
  stop)
    if grep -q hpcperfstatsd-profiler "$FAKE_DROPIN" 2>/dev/null && alive; then
      now="$(date +%s.%N)"
      printf '%s c1 hpcperfstatsd[1]: metric-profiler: cycles=4 tracked_metrics=1 tracked_types=1 overflow=0\n' "$now" >> "$S/journal"
      printf '%s c1 hpcperfstatsd[1]: metric-profiler:type=host_cpu calls=4 wall_ns=4000000 cpu_ns=2000000 wait_ns=2000000 avg_wall_ns=1000000\n' "$now" >> "$S/journal"
    fi
    stop ;;
  restart)
    stop
    dis="$(sed -n 's/^Environment=HPCPERFSTATS_DISABLE_TYPES=//p' "$FAKE_DROPIN" 2>/dev/null)"
    en="$(echo "${FAKE_TYPES}" | tr ',' '\n' | grep -vxF -f <(echo "$dis" | tr ',' '\n' | sed '/^$/d') | paste -sd, -)"
    test -n "$dis" || en="${FAKE_TYPES}"
    test -n "$dis" && test -n "${FAKE_STICKY:-}" && en="$(printf '%s,%s' "$en" "$FAKE_STICKY" | sed 's/^,//')"
    setsid "$S/hpcperfstatsd" 100000 >/dev/null 2>&1 < /dev/null &
    echo $! > "$S/pid"
    printf '%s c1 hpcperfstatsd[%s]: Enabled types: %s\n' "$(date +%s.%N)" "$!" "${en:-(none)}" >> "$S/journal"
    ;;
  *) exit 0 ;;
esac
"""
JOURNALCTL = r"""#!/usr/bin/env bash
S="${FAKE_STATE:?}"; since=0; fmt=short-unix
while test $# -gt 0; do case "$1" in --since) since="${2#@}"; shift ;; -o) fmt="$2"; shift ;; esac; shift; done
test -f "$S/journal" || exit 0
if test "$fmt" = cat; then sed 's/^[^:]*: //' "$S/journal"
else awk -v s="$since" '$1 >= s' "$S/journal"; fi
"""
RPM = r"""#!/usr/bin/env bash
case "$1" in
  -q) exit 1 ;;
  -Uvh) printf 'server localhost\nqueue default\nuser guest\npassword guest\n' > "$FAKE_SITE_CONF" ;;
  -qip) echo "Name : hpcperfstatsd" ;;
esac
exit 0
"""

SITE = "server 127.0.0.1\nqueue fake_site\nport 5672\nuser u\npassword p\nsample_freq 30\n"
IDLE_STAT = "cpu  100 0 100 10000 0 0 0 0 0 0\ncpu0 50 0 50 5000 0 0 0 0 0 0\n"


def fake_cpufreq(root: Path, driver: str = "cppc_cpufreq", governor: str = "schedutil",
                 available: str = "conservative ondemand userspace powersave performance schedutil",
                 cpus: int = 2) -> Path:
    """A cpufreq sysfs tree like c122-018's (finding 18), with ``cpus`` CPUs."""
    for cpu in range(cpus):
        d = root / f"cpu{cpu}" / "cpufreq"
        d.mkdir(parents=True)
        (d / "scaling_governor").write_text(governor + "\n")
        (d / "scaling_available_governors").write_text(available + "\n")
        (d / "scaling_driver").write_text(driver + "\n")
        (d / "scaling_cur_freq").write_text(f"{81000 + cpu * 1000}\n")
    return root


def governors(root: Path) -> list[str]:
    return [(root / f"cpu{c}" / "cpufreq" / "scaling_governor").read_text().strip()
            for c in range(2)]


def _stub(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def fake_host(tmp_path, monkeypatch):
    state = tmp_path / "state"
    bindir = tmp_path / "bin"
    etc = tmp_path / "etc"
    for d in (state, bindir, etc):
        d.mkdir()
    shutil.copy(shutil.which("sleep"), state / "hpcperfstatsd")
    for name, body in (("systemctl", SYSTEMCTL), ("journalctl", JOURNALCTL), ("rpm", RPM)):
        _stub(bindir, name, body)
    site = etc / "hpcperfstats.conf"
    site.write_text(SITE)
    run_dir = tmp_path / "run"
    paths = {"SITE_CONF": str(site), "SYSCONFIG": str(etc / "sysconfig"),
             "DROPIN_DIR": str(tmp_path / "dropin"),
             "DROPIN_PATH": str(tmp_path / "dropin" / "90-measure.conf"),
             "MEASURE_DIR": str(tmp_path / "measure"),
             "MEASURE_CONF": str(tmp_path / "measure" / "hpcperfstats.conf"),
             "PROFILER_BINARY": str(tmp_path / "measure" / "hpcperfstatsd-profiler"),
             "DISCOVERY_SECONDS": 1, "TYPES_WAIT_SECONDS": 10,
             "SYSFS_CPU": str(fake_cpufreq(tmp_path / "sys_cpu")),
             "PROC_ROOT": str(tmp_path / "proc"), "BUSY_PROBE_SECONDS": 0.05,
             "SYS_PLATFORM_DEVICES": str(tmp_path / "platform")}
    (tmp_path / "proc").mkdir()
    (tmp_path / "proc" / "stat").write_text(IDLE_STAT)
    for key, value in paths.items():
        monkeypatch.setattr(orchestrator, key, value)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_STATE", str(state))
    monkeypatch.setenv("FAKE_DROPIN", paths["DROPIN_PATH"])
    monkeypatch.setenv("FAKE_TYPES", ALL_TYPES)
    monkeypatch.setenv("FAKE_SITE_CONF", str(site))
    rpm = tmp_path / "hpcperfstatsd-3.0-7.el9.x86_64.rpm"
    rpm.write_bytes(b"rpm")
    yield {"site": site, "run": run_dir, "rpm": rpm, "paths": paths, "state": state,
           "sysfs": Path(paths["SYSFS_CPU"])}
    pid_file = state / "pid"
    if pid_file.is_file():
        try:
            os.kill(int(pid_file.read_text()), signal.SIGKILL)
        except (ProcessLookupError, ValueError):
            pass


def test_execute_full_run_on_fake_host(fake_host):
    args = orchestrator.build_parser().parse_args([
        "--rpm", str(fake_host["rpm"]), "--out", str(fake_host["run"]), "--leg-seconds", "3",
        "--settle-seconds", "2", "--fwq-seconds", "1", "--blocks", "1", "--seed", "5",
        "--fwq-quantum-us", "200", "--force", "--rmq-mgmt-port", "1", "--fwq-cpus", "0",
        "--governors", "performance,dynamic"])
    run_obj = orchestrator.Run(args)
    applied = []
    real_set = orchestrator.cpufreq.set_all

    def spy(gov, root):
        applied.append(gov)
        return real_set(gov, root)
    orchestrator.cpufreq.set_all = spy
    try:
        assert run_obj.execute() == 0
    finally:
        orchestrator.cpufreq.set_all = real_set
    run = fake_host["run"]
    profile = json.loads((run / "profile.json").read_text())
    assert profile["run_complete"] is True, profile["legs_missing"]
    # One full leg set per governor, set once before each set; originals restored on exit.
    assert applied == ["performance", "ondemand"]
    assert governors(fake_host["sysfs"]) == ["schedutil", "schedutil"]
    assert [gs["governor"] for gs in profile["governor_sets"]] == ["performance", "ondemand"]
    assert profile["metrics_governor"] == "performance"
    for leg in profile["legs"]:
        assert leg["leg_id"].startswith(leg["governor"] + "-b1-")
        assert leg["governor_observed"] == [leg["governor"]]
        assert leg["cur_freq_mhz"]["min"] == 81.0 and leg["cur_freq_mhz"]["max"] == 82.0
    head = profile["governor_sets"][1]["headline"]
    assert head["wakeups_per_s"].get("unit") == "/s" and head["wakeups_scope"] == "all_threads"
    report = (run / "report.md").read_text()
    assert "## Headline: daemon wakeups/s and CPU per governor" in report
    assert "## Headline metrics — governor `ondemand`" in report
    restore = (run / "restore.sh").read_text()
    assert "echo schedutil >" in restore and "cpu1/cpufreq/scaling_governor" in restore
    run_json = json.loads((run / "raw" / "run.json").read_text())
    assert run_json["state"]["cleanup"]["governor"] == "ok"
    assert run_json["state"]["cpufreq"]["governors"] == ["performance", "ondemand"]
    profile["legs"] = [{**leg, "leg_id": leg["leg_id"].split("-", 1)[1]} for leg in profile["legs"]
                       if leg["governor"] == "performance"]
    assert profile["groups"] == {"filesystem": ["host_cpu", "host_mem"],
                                 "likwid": ["host_cpu_hw", "intel_x86_rapl"]}
    legs = {leg["leg_id"]: leg for leg in profile["legs"]}
    assert [leg["kind"] for leg in profile["legs"]].count("SOLO") == 2
    solo = legs["b1-SOLO-likwid"]
    assert solo["status"] == "ok" and solo["verified_types"] == ["host_cpu_hw", "intel_x86_rapl"]
    assert solo["missing_types"] == []
    assert legs["b1-NONE-"]["verified_types"] == []
    # This dev host is busy, so legs are contaminated; --force turns the run into a smoke run.
    if any(leg["contaminated"] for leg in profile["legs"]):
        assert profile["not_idle"] == "smoke only"
        assert any("contaminated" in r for r in profile["flags"]["not_idle_reasons"])
    cpu = profile["metrics"]["daemon_cpu_core_s_per_h"]
    assert cpu.get("unit") == "core-s/h", cpu
    assert profile["metrics"]["fwq_mean_slowdown_pct"].get("unit") == "%"
    assert profile["provenance"]["build"]["prior_nvra"] is None
    # restore path
    assert not Path(fake_host["paths"]["DROPIN_PATH"]).exists()
    assert not Path(fake_host["paths"]["DROPIN_DIR"]).exists()
    assert not Path(fake_host["paths"]["MEASURE_DIR"]).exists()
    assert fake_host["site"].read_text() == SITE
    run_json = json.loads((run / "raw" / "run.json").read_text())
    assert run_json["flags"]["measure_queue_left"] is True
    assert run_json["state"]["cleanup"]["measure_queue"]["admin_command"].endswith("fake_site_measure")
    assert (run / "restore.sh").is_file() and (run / "report.md").is_file()
    measure_conf = (run / "raw" / "legs" / "performance-b1-NONE-" / "meta.json")
    assert measure_conf.is_file()
    assert not (fake_host["state"] / "pid").exists()  # was inactive before: stopped again


def test_execute_aborts_and_restores_on_unmapped_type(fake_host, monkeypatch):
    monkeypatch.setenv("FAKE_TYPES", "host_cpu,brand_new_collector")
    args = orchestrator.build_parser().parse_args([
        "--rpm", str(fake_host["rpm"]), "--out", str(fake_host["run"]), "--force",
        "--rmq-mgmt-port", "1", "--fwq-cpus", "0", "--fwq-quantum-us", "200",
        "--governors", "none"])
    with pytest.raises(orchestrator.driver_map.UnmappedTypeError):
        orchestrator.Run(args).execute()
    assert fake_host["site"].read_text() == SITE
    assert (fake_host["run"] / "raw" / "run.json").is_file()


def test_preflight_root_shell_allocation_user(fake_host, tmp_path, monkeypatch):
    """Regression: from a `su -` root shell the operator's own reservation was refused as
    another user's job; --allocation-user names it, and the refusal says how."""
    _stub(tmp_path / "bin", "squeue", "#!/usr/bin/env bash\necho resv_owner\n")
    monkeypatch.setenv("USER", "root")
    for var in ("SUDO_USER", "SLURM_JOB_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(orchestrator.pwd, "getpwuid", lambda uid: (_ for _ in ()).throw(KeyError(uid)))

    def preflight(*extra):
        args = orchestrator.build_parser().parse_args([
            "--rpm", str(fake_host["rpm"]), "--force", "--rmq-mgmt-port", "1", *extra])
        run = orchestrator.Run(args)
        run.out = run.raw = tmp_path
        run.host, run.flags = orchestrator.Host(tmp_path / "commands.log"), {}
        run.preflight()
        return run, [r for r in run.flags.get("not_idle_reasons", []) if "Slurm" in r]

    run, slurm = preflight()
    assert run.state["allocation_user"] == "root"
    assert slurm and "resv_owner" in slurm[0] and "--allocation-user" in slurm[0]
    run, slurm = preflight("--allocation-user", "resv_owner")
    assert run.state["allocation_user"] == "resv_owner" and slurm == []


def test_execute_aborts_when_daemon_keeps_disabled_types(fake_host, monkeypatch):
    """Regression: the daemon kept host_mem on in NONE/SOLO (stats_type_get missed it) and the
    run carried on for 8 h with every leg `ok`. Now the first such leg aborts and restores."""
    monkeypatch.setenv("FAKE_STICKY", "host_mem")
    args = orchestrator.build_parser().parse_args([
        "--rpm", str(fake_host["rpm"]), "--out", str(fake_host["run"]), "--leg-seconds", "2",
        "--settle-seconds", "1", "--fwq-seconds", "1", "--blocks", "1", "--seed", "5",
        "--fwq-quantum-us", "200", "--force", "--rmq-mgmt-port", "1", "--fwq-cpus", "0",
        "--governors", "none"])
    with pytest.raises(orchestrator.PreflightError, match=r"kept types it was told to disable: host_mem"):
        orchestrator.Run(args).execute()
    summary = json.loads((fake_host["run"] / "raw" / "legs" / "b1-NONE-" / "summary.json").read_text())
    assert summary["status"] == "type_mismatch" and summary["extra_types"] == ["host_mem"]
    assert fake_host["site"].read_text() == SITE
    assert not Path(fake_host["paths"]["DROPIN_PATH"]).exists()


def test_execute_refuses_packaged_conf_without_conf_flag(fake_host):
    fake_host["site"].write_text("server localhost\nqueue default\nuser guest\npassword guest\n")
    args = argparse.Namespace(**vars(orchestrator.build_parser().parse_args(
        ["--rpm", str(fake_host["rpm"]), "--out", str(fake_host["run"]), "--force"])))
    with pytest.raises(orchestrator.PreflightError, match="packaged localhost/guest"):
        orchestrator.Run(args).execute()


def test_execute_profile_collectors_leg_on_fake_host(fake_host):
    binary = fake_host["state"] / "hpcperfstatsd"
    args = orchestrator.build_parser().parse_args([
        "--rpm", str(fake_host["rpm"]), "--out", str(fake_host["run"]), "--leg-seconds", "2",
        "--settle-seconds", "1", "--fwq-seconds", "1", "--blocks", "1", "--seed", "5",
        "--fwq-quantum-us", "200", "--force", "--rmq-mgmt-port", "1", "--fwq-cpus", "0",
        "--governors", "none", "--profile-collectors", "--profiler-binary", str(binary)])
    assert orchestrator.Run(args).execute() == 0
    profile = json.loads((fake_host["run"] / "profile.json").read_text())
    cp = profile["collector_profile"]
    assert cp["status"] == "ok", cp
    assert cp["types"]["host_cpu"]["calls"] == 4
    prof_leg = next(leg for leg in profile["legs"] if leg["kind"] == "PROFILE")
    assert prof_leg["status"] == "ok" and prof_leg["leg_id"] == "b1-PROFILE-"
    assert not Path(fake_host["paths"]["DROPIN_PATH"]).exists()
