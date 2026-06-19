#!/usr/bin/env python3
"""Build expectations_<capability_slug>.json from build capabilities + host probes."""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from pathlib import Path

MONITOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MONITOR / "scripts"))

from lib.message_parse import (  # noqa: E402
    fast_schema_keys,
    parse_schema_counts,
    schema_key_name,
)

PROC_STAT_CPU_RE = re.compile(r"^cpu(\d+)\s")


def _read_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fd:
            return fd.readlines()
    except OSError:
        return []


def probe_host_fqdn() -> str:
    try:
        return socket.getfqdn()
    except OSError:
        return socket.gethostname()


def probe_cpu_devices() -> list[str]:
    devs: list[str] = []
    for line in _read_lines("/proc/stat"):
        m = PROC_STAT_CPU_RE.match(line)
        if m:
            devs.append(m.group(1))
    return devs


def probe_net_devices() -> list[str]:
    net_base = Path("/sys/class/net")
    if not net_base.is_dir():
        return []
    skip = {"lo"}
    return sorted(
        p.name
        for p in net_base.iterdir()
        if p.is_dir() and p.name not in skip
    )


def probe_ib_devices() -> list[str]:
    ib_base = Path("/sys/class/infiniband")
    if not ib_base.is_dir():
        return []
    devs: list[str] = []
    for hca in sorted(ib_base.iterdir()):
        if not hca.is_dir():
            continue
        ports = hca / "ports"
        if not ports.is_dir():
            continue
        for port in sorted(ports.iterdir()):
            if port.is_dir() and port.name.isdigit():
                devs.append(f"{hca.name}.{port.name}")
    return devs


def probe_block_devices() -> list[str]:
    devs: list[str] = []
    for line in _read_lines("/proc/diskstats"):
        parts = line.split()
        if len(parts) < 3:
            continue
        major = int(parts[0])
        if major < 259 and major != 8 and major != 252:
            continue
        name = parts[2]
        if name and not name[-1].isdigit():
            devs.append(name)
    return sorted(set(devs))


def default_devices_for_type(type_name: str) -> list[str] | None:
    if type_name == "host_cpu":
        cpus = probe_cpu_devices()
        return cpus if cpus else None
    if type_name == "host_net":
        return probe_net_devices()
    if type_name == "host_ib":
        return probe_ib_devices()
    if type_name == "host_block":
        return probe_block_devices()
    if type_name in (
        "host_mem",
        "host_vm",
        "host_ps",
        "host_vfs",
        "host_nfs",
        "host_numa",
        "host_sysv_shm",
        "host_tmpfs",
        "host_roofline_peak",
    ):
        return ["-"]
    if type_name == "host_proc":
        return None
    return None


def load_capabilities(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def schema_from_shm(shm_dir: Path) -> dict[str, list[str]] | None:
    schema_path = shm_dir / "schema"
    if not schema_path.is_file():
        return None
    text = schema_path.read_text(encoding="utf-8")
    if not text.lstrip().startswith("$"):
        return None
    return parse_schema_counts(text)


def build_manifest(
    capabilities: dict,
    *,
    shm_dir: Path | None,
    enable_slow_tier: bool,
) -> dict:
    slug = capabilities["capability_slug"]
    schema = schema_from_shm(shm_dir) if shm_dir else None
    types: dict[str, dict] = {}
    if schema:
        for type_name, keys in schema.items():
            entry = {
                "schema_keys": keys,
                "fast_keys": fast_schema_keys(keys),
                "schema_key_names": [schema_key_name(k) for k in keys],
            }
            devices = default_devices_for_type(type_name)
            if devices is not None:
                entry["devices"] = devices
            types[type_name] = entry

    return {
        "capability_slug": slug,
        "compile_capabilities": capabilities,
        "host_fqdn": probe_host_fqdn(),
        "program_version": capabilities.get("package_version", "unknown"),
        "enable_slow_tier": enable_slow_tier,
        "types": types,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Build expectations manifest for shm validation")
    p.add_argument("--capabilities", type=Path, required=True)
    p.add_argument("--shm-dir", type=Path, default=None)
    p.add_argument("--enable-slow-tier", type=int, default=1, choices=(0, 1))
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    caps = load_capabilities(args.capabilities)
    slug = caps["capability_slug"]
    shm_dir = args.shm_dir
    if shm_dir is None:
        env = os.environ.get("HPCPERFSTATS_DEBUG_SHM_DIR", "/dev/shm/hpcperfstatsd-debug")
        shm_dir = Path(env)

    manifest = build_manifest(
        caps,
        shm_dir=shm_dir,
        enable_slow_tier=bool(args.enable_slow_tier),
    )
    if not manifest["types"]:
        print(
            "build_message_expectations: no schema from shm; start daemon or pass --shm-dir",
            file=sys.stderr,
        )
        return 1

    out = args.out or args.capabilities.parent / f"expectations_{slug}.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
