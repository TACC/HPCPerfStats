"""Orchestrator: dry-run plan, drop-ins, measure conf, preflight refusals, root gate."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from lib import orchestrator

ROOT = Path(__file__).resolve().parents[1]
MONITOR = ROOT.parent
HARNESS = ROOT / "bin" / "run_overhead_profile.sh"

SITE_CONF = """\
server rmq.example.org
queue  stampede3
port   5672
user   monitor
password s3cret
sample_freq 30
sample_freq_slow 600
send_freq 300
enable_slow_tier 1
"""


def _dry(*extra, env=None):
    cmd = ["bash", str(HARNESS), "--dry-run", "--rpm", "/tmp/hpcperfstatsd-3.0-7.el9.x86_64.rpm",
           "--dry-run-types", "host_cpu,host_mem,host_cpu_hw,intel_x86_rapl", *extra]
    return subprocess.run(cmd, capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def test_run_overhead_profile_dry_run():
    res = _dry("--seed", "7")
    assert res.returncode == 0, res.stderr
    out = res.stdout
    assert "Schedule (seed 7" in out
    assert "Projected end:" in out
    assert re.search(r"\bOFF\b.*\n.*\bNONE\b.*\n.*\bALL\b", out)
    assert "SOLO filesystem" in out and "SOLO likwid" in out
    assert "restore trap:" in out.lower()
    assert "rpm -Uvh --replacepkgs /tmp/hpcperfstatsd-3.0-7.el9.x86_64.rpm" in out
    assert "CADENCE" not in out
    sweep = _dry("--seed", "7", "--cadence-sweep", "60,300")
    assert sweep.returncode == 0, sweep.stderr
    assert "CADENCE sample_freq=60" in sweep.stdout


def test_dry_run_unfittable_aborts_with_min_hours():
    res = _dry("--max-hours", "1")
    assert res.returncode != 0
    assert "minimum" in (res.stdout + res.stderr).lower()


def test_real_run_refuses_without_root():
    if os.geteuid() == 0:
        pytest.skip("running as root")
    res = subprocess.run(["bash", str(HARNESS), "--rpm", "/tmp/x.rpm"], capture_output=True,
                         text=True)
    assert res.returncode != 0
    assert "root" in res.stderr.lower()


def test_profile_collectors_adds_labelled_profile_leg():
    res = _dry("--seed", "7", "--profile-collectors")
    assert res.returncode == 0, res.stderr
    assert "PROFILE" in res.stdout and "instrumented build" in res.stdout
    plain = _dry("--seed", "7")
    assert "PROFILE" not in plain.stdout


def test_profile_dropin_runs_profiler_binary_on_production_conf():
    active = ["host_cpu", "host_mem"]
    groups = {"filesystem": active}
    text = orchestrator.dropin_text("PROFILE", active, groups, binary=orchestrator.PROFILER_BINARY)
    assert "HPCPERFSTATS_DISABLE_TYPES" not in text
    assert f"ExecStart={orchestrator.PROFILER_BINARY} -c {orchestrator.SITE_CONF}" in text
    assert orchestrator.MEASURE_CONF not in text


def test_dropin_disable_csv():
    active = ["host_cpu", "host_cpu_hw", "host_mem", "intel_x86_rapl"]
    groups = {"filesystem": ["host_cpu", "host_mem"], "likwid": ["host_cpu_hw", "intel_x86_rapl"]}
    none = orchestrator.dropin_text("NONE", active, groups)
    assert "Environment=HPCPERFSTATS_DISABLE_TYPES=host_cpu,host_cpu_hw,host_mem,intel_x86_rapl" in none
    assert "ExecStart=\nExecStart=/usr/sbin/hpcperfstatsd -c " + orchestrator.MEASURE_CONF in none
    solo = orchestrator.dropin_text("SOLO", active, groups, group="likwid")
    assert "HPCPERFSTATS_DISABLE_TYPES=host_cpu,host_mem\n" in solo
    assert orchestrator.MEASURE_CONF in solo
    assert orchestrator.dropin_text("ALL", active, groups) is None
    assert orchestrator.dropin_text("OFF", active, groups) is None
    cad = orchestrator.dropin_text("CADENCE", active, groups)
    assert "HPCPERFSTATS_DISABLE_TYPES" not in cad and orchestrator.MEASURE_CONF in cad


def test_measure_conf_only_changes_queue():
    conf = orchestrator.measure_conf_text(SITE_CONF)
    assert "queue stampede3_measure\n" in conf
    assert "server rmq.example.org" in conf and "password s3cret" in conf
    assert conf.count("queue") == 1
    cad = orchestrator.measure_conf_text(SITE_CONF, sample_freq=60)
    assert "sample_freq 60\n" in cad and "sample_freq_slow 600" in cad
    slow = orchestrator.measure_conf_text(SITE_CONF, sample_freq=900)
    assert "sample_freq_slow 900" in slow
    with pytest.raises(orchestrator.PreflightError):
        orchestrator.measure_conf_text(SITE_CONF.replace("stampede3", "x_measure"))


def test_daemon_reads_conf_after_cli_so_queue_needs_a_conf_copy():
    """Contract behind MEASURE_CONF: conf `queue` overrides CLI `-q` (CLI parsed first)."""
    main = (MONITOR / "src" / "monitor.c").read_text()
    assert main.index("monitor_cli_parse_args(argc, argv") < main.index("read_conf_file();")


def test_preflight_refuses_disabled_collectors():
    assert orchestrator.check_site_conf(SITE_CONF) == []
    bad = orchestrator.check_site_conf(SITE_CONF + "disable_types host_proc\n")
    assert bad and "disable_types" in bad[0]
    assert orchestrator.check_site_conf(SITE_CONF + "collection_profile minimal\n")
    assert orchestrator.check_site_conf(SITE_CONF + "collection_profile default\n") == []
    assert orchestrator.check_site_conf(SITE_CONF + "# disable_types x\n") == []
    assert orchestrator.check_site_conf("server x\n")[0].startswith("conf has no `queue`")
    sysconfig = (MONITOR / "src" / "hpcperfstatsd.sysconfig").read_text()
    assert orchestrator.check_sysconfig(sysconfig) == []
    bad = orchestrator.check_sysconfig(sysconfig + "HPCPERFSTATS_DISABLE_TYPES=host_proc\n")
    assert bad and "HPCPERFSTATS_DISABLE_TYPES" in bad[0]
    bad = orchestrator.check_sysconfig("HPCPERFSTATS_COLLECTION_PROFILE=minimal\n")
    assert bad and "HPCPERFSTATS_COLLECTION_PROFILE" in bad[0]
    packaged = (MONITOR / "src" / "hpcperfstats.conf").read_text()
    assert orchestrator.is_packaged_default_conf(packaged)
    assert not orchestrator.is_packaged_default_conf(SITE_CONF)


def test_preflight_slurm_and_load():
    assert orchestrator.check_slurm_jobs("alice\nalice\n", "alice") == []
    assert orchestrator.check_slurm_jobs("", "alice") == []
    assert orchestrator.check_slurm_jobs("alice\nbob\n", "alice") == ["bob"]
    assert orchestrator.check_load(1.0, 28) is None
    assert "load" in orchestrator.check_load(5.0, 28)


def test_invoking_user_finds_reservation_owner_from_root_shell(tmp_path, monkeypatch):
    """Regression: on Horizon c122-018 (`su -`, so SUDO_USER unset and USER=root) the
    preflight refused the operator's own `sharrell` reservation as another user's job."""
    monkeypatch.setattr(orchestrator.pwd, "getpwuid",
                        lambda uid: {812345: type("P", (), {"pw_name": "sharrell"})}[uid])
    loginuid = tmp_path / "loginuid"
    root_shell = {"USER": "root"}

    loginuid.write_text("812345")
    assert orchestrator.invoking_user(root_shell, loginuid) == "sharrell"
    assert orchestrator.check_slurm_jobs("sharrell\n", orchestrator.invoking_user(root_shell, loginuid)) == []
    assert orchestrator.invoking_user({"USER": "root", "SUDO_USER": "alice"}, loginuid) == "alice"
    assert orchestrator.invoking_user({"USER": "root", "SLURM_JOB_USER": "bob"}, loginuid) == "bob"
    # Root login (uid 0), unset audit uid, unknown uid, junk, missing file: fall back to USER.
    for text in ("0", "4294967295", "999", "garbage"):
        loginuid.write_text(text)
        assert orchestrator.invoking_user(root_shell, loginuid) == "root", text
    assert orchestrator.invoking_user(root_shell, tmp_path / "missing") == "root"
    assert orchestrator.invoking_user({}, tmp_path / "missing") is None
    # A root login still has the explicit override.
    args = orchestrator.build_parser().parse_args(["--rpm", "x.rpm", "--allocation-user", "sharrell"])
    assert args.allocation_user == "sharrell"


def test_restore_script_restores_conf_and_state(tmp_path):
    text = orchestrator.restore_script_text(saved_conf=tmp_path / "site.conf",
                                            was_active=True, was_enabled=False,
                                            measure_queue="stampede3_measure")
    assert "rm -f " + orchestrator.DROPIN_PATH in text
    assert "rmdir " + orchestrator.DROPIN_DIR in text
    assert "systemctl daemon-reload" in text
    assert f"install -m 0600 {tmp_path / 'site.conf'} /etc/hpcperfstats/hpcperfstats.conf" in text
    assert "systemctl disable hpcperfstats" in text and "systemctl restart hpcperfstats" in text
    assert "stampede3_measure" in text
    assert "rpm -e" not in text
    assert subprocess.run(["bash", "-n"], input=text, text=True).returncode == 0


def test_no_idle_force_flag_marks_smoke_only():
    flags = orchestrator.idle_flags(forced=True, problems=["load 5.0 >= 2.8"])
    assert flags == {"not_idle": "smoke only", "not_idle_reasons": ["load 5.0 >= 2.8"]}
    assert orchestrator.idle_flags(forced=True, problems=[]) == {}
    with pytest.raises(orchestrator.PreflightError):
        orchestrator.idle_flags(forced=False, problems=["load"])
