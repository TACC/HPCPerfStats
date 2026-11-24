"""bin/campaign_node_run.sh on a fake node: every gate refuses before the build/launch.

c313-031 2026-11-23: the same steps pasted as a 6 KB block lost lines in the tty buffer while
a command ran; c573-002 the same day launched past a failing `make check`. The script runs the
gates as one file; these tests drive it with stub commands on PATH.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "campaign_node_run.sh"

STUBS = {
    "hostname": "echo c313-031",
    "pkill": "exit 1",
    "pgrep": '[ "$1" = -af ] && echo "123 overhead_profile.py"; [ "$1" = -af ]',
    "squeue": 'echo "${SQ-47:00:00}"',
    "make": ('case "$*" in *check*) echo "# TOTAL: 152"; [ -z "${CF:-}" ] || '
             '{ echo "FAIL: test_x"; exit 2; };; esac'),
    "rpmbuild": ("mkdir -p rpmbuild/RPMS/x86_64; cd rpmbuild/RPMS/x86_64; "
                 "touch hpcperfstatsd-3.1-1.el8.x86_64.rpm "
                 "hpcperfstatsd-debuginfo-3.1-1.el8.x86_64.rpm"),
    "rpm": ('case "$1" in --eval) echo .el8;; -qp) echo 3.1-1.el8;; *) '
            '[ -n "${INSTV:-}" ] && echo "$INSTV" && exit 0; '
            'echo "package hpcperfstatsd is not installed"; exit 1;; esac'),
    "systemctl": 'echo "${XPUM:-inactive}"',
    "lspci": "true",
    "uname": 'echo "${ARCH:-x86_64}"',
    "setsid": '"$@"',
    "timeout": 'shift 2; "$@"',
    "sleep": "true",
    "gcc": "true", "autoreconf": "true", "cmake": "true",
}
PREPARE = """#!/bin/bash
echo "ALLOW=$HPCS_ALLOW_DEFAULT_BROKER"
mkdir -p rpmbuild/SOURCES rpmbuild/SPECS rpmbuild/build-info
touch rpmbuild/SOURCES/hpcperfstats-3.1.tar.gz
echo '{"configure":{"cpu_backend":"x86"}}' \
  > rpmbuild/build-info/monitor-build-capabilities.json
"""


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def node(tmp_path: Path) -> Path:
    """A fake monitor checkout (site conf points at localhost, as on LS6) plus stub commands."""
    mon = tmp_path / "w" / "monitor"
    (mon / "measurement" / "bin").mkdir(parents=True)
    shutil.copy(SCRIPT, mon / "measurement" / "bin" / SCRIPT.name)
    _exe(mon / "measurement" / "bin" / "run_overhead_profile.sh",
         '#!/bin/bash\necho "harness $* py=$HPCPERFSTATS_PYTHON"\n')
    (mon / "scripts" / "fleet").mkdir(parents=True)
    (mon / "scripts" / "fleet" / "ls6.force").touch()
    _exe(mon / "scripts" / "build_static_bundle.sh", "#!/bin/bash\n")
    _exe(mon / "scripts" / "prepare_rpmbuild_ls6.sh", PREPARE)
    (mon / "site").mkdir()
    (mon / "site" / "hpcperfstats.site.conf").write_text(
        "server localhost\nqueue default\nuser guest\npassword guest\n")
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "hpcperfstats.conf").write_text(
        "server stats.ls6.example\nqueue ls6\nuser guest\npassword secret\n")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in STUBS.items():
        _exe(stubs / name, f"#!/bin/bash\n{body}\n")
    (tmp_path / "home").mkdir()
    return tmp_path


def _run(node: Path, cluster: str = "ls6", **env: str) -> subprocess.CompletedProcess:
    full = {"PATH": f"{node / 'stubs'}:/usr/bin:/bin", "HOME": str(node / "home"),
            "HPCS_ETC_CONF": str(node / "etc" / "hpcperfstats.conf"),
            "HPCS_SYSCONFIG": str(node / "etc" / "sysconfig"),
            "HPCS_DROPIN_DIR": str(node / "etc" / "dropins"), **env}
    script = node / "w" / "monitor" / "measurement" / "bin" / SCRIPT.name
    return subprocess.run(["bash", str(script), cluster], capture_output=True, text=True,
                          env=full, timeout=60, check=False)


def _log(node: Path) -> str:
    path = node / "w" / "test_runs" / "overhead" / "v31_c313-031.log"
    return path.read_text() if path.exists() else ""


def test_success_builds_and_launches_with_the_campaign_conf(node: Path) -> None:
    res = _run(node)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "broker=stats.ls6.example queue=ls6" in res.stdout
    assert "password REDACTED" in res.stdout and "secret" not in res.stdout
    conf = (node / "home" / "hps_campaign" / "measure.conf").read_text().splitlines()
    assert conf == ["server stats.ls6.example", "queue ls6", "port 5672", "user guest",
                    "password secret", "buffer_hours 6", "sample_freq 30",
                    "sample_freq_slow 600", "send_freq 300", "enable_slow_tier 1",
                    "emit_value_sentinels 1"]
    harness = _log(node)
    for arg in ("--leg-seconds 1800", "--settle-seconds 60", "--fwq-seconds 300",
                "--blocks 3", "--max-hours 20", "--governors performance",
                "hpcperfstatsd-3.1-1.el8.x86_64.rpm"):
        assert arg in harness
    assert "debuginfo" not in harness
    assert not (node / "w" / "monitor" / "scripts" / "fleet" / "ls6.force").exists()
    prep = (node / "w" / "test_runs" / "overhead" / "c313-031_31_prepare.log").read_text()
    assert "ALLOW=1" in prep


@pytest.mark.parametrize("env, reason", [
    ({"CF": "1"}, "make check failed"),
    ({"INSTV": "3.1-1.el8"}, "sorts >= 3.1-1.el8"),
    ({"SQ": "10:00:00"}, "need >= 16 h left"),
    ({"SQ": ""}, "need >= 16 h left"),
    ({"XPUM": "active"}, "xpum.service active"),
    ({"ARCH": "aarch64"}, "not cpu_backend arm"),
])
def test_each_gate_stops_without_launching(node: Path, env: dict, reason: str) -> None:
    res = _run(node, **env)
    assert res.returncode == 1
    assert "STOP: " in res.stdout and reason in res.stdout, res.stdout
    assert "NOT launched" in res.stdout
    assert _log(node) == ""


def test_failed_check_never_builds_the_rpm(node: Path) -> None:
    res = _run(node, CF="1")
    assert not (node / "w" / "monitor" / "rpmbuild").exists()
    # check-local failures print no FAIL: line (c313-031), so the log's end is shown: the
    # TOTAL line appears once from the summary grep and again in the tail.
    assert res.stdout.count("# TOTAL: 152") == 2, res.stdout


def test_older_installed_package_is_fine(node: Path) -> None:
    assert _run(node, INSTV="3.0-15").returncode == 0


def test_localhost_everywhere_stops(node: Path) -> None:
    (node / "etc" / "hpcperfstats.conf").write_text(
        "server localhost\nqueue ls6\nuser guest\npassword guest\n")
    res = _run(node)
    assert "broker is localhost" in res.stdout and res.returncode == 1


def test_sysconfig_leftover_stops_but_comments_pass(node: Path) -> None:
    sc = node / "etc" / "sysconfig"
    sc.write_text("# comment\n\n   # indented\n")
    assert _run(node).returncode == 0
    sc.write_text("# comment\nHPCPERFSTATS_DISABLE_TYPES=host_ib\n")
    res = _run(node)
    assert "has settings" in res.stdout and res.returncode == 1


def test_unknown_cluster_is_a_usage_error(node: Path) -> None:
    res = _run(node, cluster="frontera")
    assert res.returncode == 2 and "usage:" in res.stderr


def test_script_is_executable_and_parses() -> None:
    assert os.access(SCRIPT, os.X_OK)
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
