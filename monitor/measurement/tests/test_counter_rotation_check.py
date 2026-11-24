"""bin/counter_rotation_check.sh: picks the event sets for the CPU class and refuses to run
beside the daemon (the counterfactual for the c573-002 counter rotation numbers, 2026-11-24)."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "counter_rotation_check.sh"

CPUINFO = {
    "milan": "vendor_id\t: AuthenticAMD\ncpu family\t: 25\nmodel\t\t: 1\n",
    "turin": "vendor_id\t: AuthenticAMD\ncpu family\t: 26\nmodel\t\t: 2\n",
    "skx": "vendor_id\t: GenuineIntel\ncpu family\t: 6\nmodel\t\t: 85\n",
    "grace": "processor\t: 0\nCPU implementer\t: 0x41\nCPU part\t: 0xd4f\n",
    "other": "vendor_id\t: HygonGenuine\ncpu family\t: 24\n",
}
STAT = "cpu  100 0 50 10000 0 7 3 0 0 0\nintr 123456 0 0\n"


def _exe(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    mon = tmp_path / "monitor" / "measurement" / "bin"
    mon.mkdir(parents=True)
    (mon / SCRIPT.name).write_text(SCRIPT.read_text())
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _exe(stubs / "hostname", "echo c492-081")
    _exe(stubs / "pgrep", 'case "$*" in *hpcperfstatsd*) [ -n "${DAEMON:-}" ];; *) exit 1;; esac')
    _exe(stubs / "sleep", "true")
    # perf stat -a -x, -e EVENTS -o FILE -- ...: record the events, write one CSV row each.
    _exe(stubs / "perf", 'ev=""; out=""; while [ $# -gt 0 ]; do case "$1" in -e) ev="$2"; shift 2;; '
         '-o) out="$2"; shift 2;; --) break;; *) shift;; esac; done; echo "$ev" >> "$PERF_LOG"; '
         'IFS=, read -r -a a <<<"$ev"; for e in "${a[@]}"; do echo "100,,$e,1000,50.00,," >> "$out"; '
         'done')
    (tmp_path / "stat").write_text(STAT)
    return tmp_path


def _run(tree: Path, cpu: str, **env: str) -> subprocess.CompletedProcess:
    (tree / "cpuinfo").write_text(CPUINFO[cpu])
    full = {"PATH": f"{tree / 'stubs'}:/usr/bin:/bin", "HPCS_CPUINFO": str(tree / "cpuinfo"),
            "HPCS_PROC_STAT": str(tree / "stat"), "HPCS_CHECK_SECONDS": "1",
            "PERF_LOG": str(tree / "perf.log"), **env}
    return subprocess.run(["bash", str(tree / "monitor" / "measurement" / "bin" / SCRIPT.name)],
                          capture_output=True, text=True, env=full, timeout=60, check=False)


@pytest.mark.parametrize("cpu, fit, monitor", [
    ("milan", "cycles,instructions,r00c2,r00c3,rff03", "cycles,instructions,r00c2,r00c3,rff03,r0729"),
    ("turin", "cycles,instructions,r00c2,r00c3,r0f03",
     "cycles,instructions,r00c2,r00c3,r0729,r0f03,raf03,r8f03"),
    ("skx", "instructions,cycles,ref-cycles,r01c7,r02c7,r04c7,r08c7,r10c7,r20c7,r40c7,r80c7",
     "instructions,cycles,ref-cycles,r01c7,r02c7,r04c7,r08c7,r10c7,r20c7,r40c7,r80c7,"
     "r0151,r01d1,r02d1,r04d1"),
    ("grace", "cycles,r08,r801c,r8018,r8014,r80c1,r80c0",
     "cycles,r08,r801c,r8018,r8014,r80c1,r80c0,r80e3,r80e7"),
])
def test_event_sets_follow_the_cpu_class(tree: Path, cpu: str, fit: str, monitor: str) -> None:
    res = _run(tree, cpu)
    assert res.returncode == 0, res.stdout + res.stderr
    assert (tree / "perf.log").read_text().splitlines() == [fit, monitor]
    for label in ("none", "fit", "monitor"):
        assert f"\n{label} " in "\n" + res.stdout
    assert "nmi_watchdog=" in res.stdout and "other perf_event holders" in res.stdout
    saved = list((tree / "monitor" / "measurement" / "results").glob("counter_rotation_*.txt"))
    assert len(saved) == 1 and "monitor " in saved[0].read_text()
    # each run lists only its own events (the output file is emptied before every perf run)
    mon_block = res.stdout.split("\nmonitor ")[1]
    assert mon_block.count("running") == len(monitor.split(","))


def test_refuses_while_the_daemon_runs(tree: Path) -> None:
    res = _run(tree, "milan", DAEMON="1")
    assert res.returncode == 1 and "STOP: hpcperfstatsd" in res.stdout
    assert not (tree / "perf.log").exists()


def test_unknown_cpu_class_stops(tree: Path) -> None:
    res = _run(tree, "other")
    assert res.returncode == 1 and "STOP: no event sets" in res.stdout


def test_script_is_executable_and_parses() -> None:
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
