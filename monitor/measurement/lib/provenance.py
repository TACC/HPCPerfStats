"""Provenance for cross-architecture comparison (section E of the plan).

Each probe runs with a timeout; a missing tool or file records ``None`` with the error, so
provenance never aborts a run. Secrets in the conf are redacted before they are written.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

from .cadence import parse_monitor_conf

_SECRET_RE = re.compile(r"^(\s*(?:password|user)\s*[:= \t]\s*)(\S.*)$", re.I | re.M)
GPU_LSPCI_RE = re.compile(r"(VGA compatible|3D controller|Display controller)", re.I)


def run(cmd: list[str], timeout: float = 20.0) -> dict[str, Any]:
    if shutil.which(cmd[0]) is None:
        return {"cmd": cmd, "ok": False, "error": f"{cmd[0]} not found"}
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as err:
        return {"cmd": cmd, "ok": False, "error": str(err)}
    return {"cmd": cmd, "ok": res.returncode == 0, "rc": res.returncode,
            "stdout": res.stdout[-200000:], "stderr": res.stderr[-4000:]}


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return None


def redact_conf(text: str) -> str:
    return _SECRET_RE.sub(lambda m: m.group(1) + "REDACTED", text)


def sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def cpuinfo_fields(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, val = line.partition(":")
        key = key.strip()
        if sep and key in ("vendor_id", "model name", "cpu family", "model", "stepping",
                           "microcode", "CPU implementer", "CPU part", "CPU variant") and key not in out:
            out[key] = val.strip()
    return out


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "unknown"


def host_info() -> dict[str, Any]:
    cpu = cpuinfo_fields(_read("/proc/cpuinfo") or "")
    lscpu = run(["lscpu", "-J"])
    model = cpu.get("model name")
    if not model and lscpu.get("ok"):
        try:
            for item in json.loads(lscpu["stdout"]).get("lscpu", []):
                if item.get("field", "").startswith("Model name"):
                    model = item.get("data")
        except json.JSONDecodeError:
            pass
    lspci = run(["lspci"])
    gpus = [ln for ln in (lspci.get("stdout") or "").splitlines() if GPU_LSPCI_RE.search(ln)]
    smi = run(["nvidia-smi", "-L"]) if shutil.which("nvidia-smi") else None
    gov = _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    fqdn = socket.getfqdn()
    return {
        "hostname": fqdn, "short": fqdn.split(".")[0], "arch": platform.machine(),
        "cpu_model": model or "unknown", "cpu": cpu, "lscpu": lscpu,
        "nproc_online": os.cpu_count(),
        "smt_active": (_read("/sys/devices/system/cpu/smt/active") or "").strip() or None,
        "numa_nodes": len(list(Path("/sys/devices/system/node").glob("node[0-9]*"))),
        "mem_total_kib": next((int(ln.split()[1]) for ln in (_read("/proc/meminfo") or "").splitlines()
                               if ln.startswith("MemTotal:")), None),
        "kernel": platform.release(), "cmdline": (_read("/proc/cmdline") or "").strip(),
        "os_release": _read("/etc/os-release"),
        "cpufreq_governor": gov.strip() if gov else None,
        "perf_event_paranoid": (_read("/proc/sys/kernel/perf_event_paranoid") or "").strip() or None,
        "gpus": gpus, "nvidia_smi": smi,
    }


def git_info(checkout: Path) -> dict[str, Any]:
    head = run(["git", "-C", str(checkout), "rev-parse", "HEAD"])
    dirty = run(["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=no"])
    return {"commit": (head.get("stdout") or "").strip() or None,
            "dirty": bool((dirty.get("stdout") or "").strip()) if dirty.get("ok") else None}


def tool_versions() -> dict[str, Any]:
    out = {"python": platform.python_version()}
    for name, cmd in (("gcc", ["gcc", "--version"]), ("perf", ["perf", "--version"]),
                      ("ss", ["ss", "-V"]), ("rpm", ["rpm", "--version"]),
                      ("systemd", ["systemctl", "--version"])):
        r = run(cmd, timeout=5)
        out[name] = (r.get("stdout") or r.get("error") or "").splitlines()[0] if (
            r.get("stdout") or r.get("error")) else None
    return out


def build_info(rpm: Path | None, build_info_dir: Path | None, conf_text: str | None,
               checkout: Path, unit_props: dict[str, str] | None, prior_nvra: str | None,
               binary: Path = Path("/usr/sbin/hpcperfstatsd")) -> dict[str, Any]:
    caps = None
    caps_path = (build_info_dir / "monitor-build-capabilities.json") if build_info_dir else None
    if caps_path and caps_path.is_file():
        caps = json.loads(caps_path.read_text())
    return {
        "rpm_path": str(rpm) if rpm else None,
        "rpm_sha256": sha256_file(rpm) if rpm else None,
        "rpm_qip": run(["rpm", "-qip", str(rpm)]) if rpm else None,
        "prior_nvra": prior_nvra,
        "binary_sha256": sha256_file(binary),
        "git": git_info(checkout),
        "build_capabilities": caps if caps is not None else {
            "status": "NOT_MEASURED", "reason": "not emitted (release capabilities are best-effort)"},
        "conf_redacted": redact_conf(conf_text) if conf_text else None,
        "cadence": parse_monitor_conf(conf_text) if conf_text else None,
        "unit_properties": unit_props,
        "tools": tool_versions(),
    }
