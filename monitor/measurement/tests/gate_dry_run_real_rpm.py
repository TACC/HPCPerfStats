#!/usr/bin/env python3
"""Gate check (not collected by pytest): harness dry run against the release RPM built here.

Needs ``monitor/rpmbuild/`` from ``prepare_rpmbuild_dirs.sh`` + ``rpmbuild -ba``. Asserts the
RPM is the spec's NVRA, its payload is the documented %files set (the build-info stash stays
outside the buildroot), build-info/monitor-build-capabilities.json was stashed, and the
dry run resolves driver groups from those capabilities and prints the schedule.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

MONITOR = Path(__file__).resolve().parents[2]
TOP = MONITOR / "rpmbuild"
EXPECTED_PAYLOAD = {
    "/etc/hpcperfstats", "/etc/hpcperfstats/hpcperfstats.conf", "/etc/sysconfig/hpcperfstatsd",
    "/usr/lib/systemd/system/hpcperfstats.service", "/usr/libexec/hpcperfstats",
    "/usr/libexec/hpcperfstats/stubs", "/usr/libexec/hpcperfstats/stubs/lshw",
    "/usr/sbin/hpcperfstatsd", "/var/lib/hpcperfstats", "/var/lib/hpcperfstats/dump",
}


def main() -> int:
    rpms = sorted((TOP / "RPMS" / platform.machine()).glob("hpcperfstatsd-*.rpm"))
    assert len(rpms) == 1, rpms
    rpm = rpms[0]
    spec_nvr = subprocess.run(["rpmspec", "-q", "--queryformat", "%{name}-%{version}-%{release}\n",
                               str(MONITOR / "hpcperfstats.spec")], capture_output=True, text=True,
                              check=True).stdout.splitlines()[0]
    nvr = subprocess.run(["rpm", "-qp", "--queryformat", "%{name}-%{version}-%{release}", str(rpm)],
                         capture_output=True, text=True, check=True).stdout
    assert nvr == spec_nvr, (nvr, spec_nvr)
    payload = set(subprocess.run(["rpm", "-qlp", str(rpm)], capture_output=True, text=True,
                                 check=True).stdout.split())
    # rpmbuild adds /usr/lib/.build-id links for the ELF binary on every build.
    payload = {p for p in payload if not p.startswith("/usr/lib/.build-id")}
    assert payload == EXPECTED_PAYLOAD, sorted(payload ^ EXPECTED_PAYLOAD)
    caps_path = TOP / "build-info" / "monitor-build-capabilities.json"
    caps = json.loads(caps_path.read_text())
    assert caps["configure"]["debug"] is False, caps["configure"]
    res = subprocess.run(["bash", str(MONITOR / "measurement" / "bin" / "run_overhead_profile.sh"),
                          "--dry-run", "--rpm", str(rpm), "--build-info", str(TOP / "build-info"),
                          "--seed", "11", "--dry-run-types",
                          "host_cpu,host_mem,host_cpu_hw,intel_x86_rapl,host_ib"],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = res.stdout
    expect_cpu = "likwid" if caps["configure"]["cpu_backend"] == "likwid" else "arm_cpu"
    assert f"{expect_cpu}: host_cpu_hw" in out.replace(",intel_x86_rapl", "") or \
        f"{expect_cpu}: host_cpu_hw,intel_x86_rapl" in out, out
    for needle in ("Schedule (seed 11", "Projected end:", f"rpm -Uvh --replacepkgs {rpm}",
                   "Restore trap:"):
        assert needle in out, needle
    print(out)
    print(f"REAL_RPM_DRY_RUN_OK {nvr} caps={caps['capability_slug']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
