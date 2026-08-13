#!/usr/bin/env python3
"""Emit monitor-build-capabilities.json for a configured Autotools build tree.

Run from HPCPerfStats/monitor/:
  .build-static/../.venv/bin/python scripts/emit_build_capabilities.py --build-dir .build-static
  make -C .build-static capabilities
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

MONITOR = Path(__file__).resolve().parents[1]
DEFAULT_OUT = "monitor-build-capabilities.json"

_RE_CONFIG_LOG_BOOL = re.compile(
    r"^\s*(?:infiniband|gpu|amd_gpu|lustre|beegfs|opa|hardware|debug)='(true|false)'",
    re.MULTILINE,
)
_RE_PACKAGE_VERSION = re.compile(r'#define\s+PACKAGE_VERSION\s+"([^"]+)"')
_RE_MONITOR_WITH = re.compile(r"-DMONITOR_WITH_([A-Z_]+)")
_RE_CPU_BACKEND = re.compile(r"-DMONITOR_CPU_BACKEND_([A-Z]+)")
_RE_IB_MAD_DLOPEN = re.compile(r"-DMONITOR_IB_MAD_DLOPEN\b")
_RE_OPA_MAD_DLOPEN = re.compile(r"-DMONITOR_OPA_MAD_DLOPEN\b")
_RE_PAPI_FLOPS = re.compile(r"-DMONITOR_CPU_PAPI_FLOPS\b")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_config_log(build_dir: Path) -> dict[str, bool]:
    text = _read_text(build_dir / "config.log")
    out: dict[str, bool] = {}
    for m in _RE_CONFIG_LOG_BOOL.finditer(text):
        key = m.group(0).split("=")[0].strip().rstrip("'").split()[-1]
        if key.endswith("'"):
            key = key[:-1]
        # line is like: infiniband='true'
        line = m.group(0).strip()
        name, val = line.split("=", 1)
        name = name.strip()
        val = val.strip().strip("'")
        out[name] = val == "true"
    return out


def _parse_src_makefile(
    build_dir: Path,
) -> tuple[set[str], str | None, bool, bool, bool, bool]:
    text = _read_text(build_dir / "src" / "Makefile")
    features = set(_RE_MONITOR_WITH.findall(text))
    cpu_m = _RE_CPU_BACKEND.search(text)
    cpu_backend = cpu_m.group(1).lower() if cpu_m else None
    debug = "-DDEBUG" in text
    ib_mad_dlopen = bool(_RE_IB_MAD_DLOPEN.search(text))
    opa_mad_dlopen = bool(_RE_OPA_MAD_DLOPEN.search(text))
    papi_hybrid = bool(_RE_PAPI_FLOPS.search(text))
    return features, cpu_backend, debug, ib_mad_dlopen, opa_mad_dlopen, papi_hybrid


def _package_version(build_dir: Path) -> str:
    for candidate in (build_dir / "config.h", build_dir / "src" / "config.h"):
        text = _read_text(candidate)
        m = _RE_PACKAGE_VERSION.search(text)
        if m:
            return m.group(1)
    ac_text = _read_text(MONITOR / "configure.ac")
    m = re.search(r"AC_INIT\(\[hpcperfstats\],\s*\[([^\]]+)\]", ac_text)
    if m:
        return m.group(1)
    return "unknown"


def _host_cpu(build_dir: Path) -> str:
    text = _read_text(build_dir / "config.status")
    m = re.search(r"host_cpu='([^']+)'", text)
    if m:
        return m.group(1)
    return platform.machine()


def _normalize_arch(host_cpu: str) -> str:
    cpu = host_cpu.lower()
    if cpu in ("aarch64", "arm64"):
        return "aarch64"
    if cpu in ("x86_64", "amd64"):
        return "x86_64"
    return re.sub(r"[^a-z0-9]+", "_", cpu)


def build_capability_slug(
    *,
    arch: str,
    version: str,
    debug: bool,
    features: set[str],
    cpu_backend: str | None,
    tier: str,
    ib_mad_dlopen: bool = False,
    opa_mad_dlopen: bool = False,
) -> str:
    parts: list[str] = [arch, f"ver{version}"]
    if debug:
        parts.append("debug")
    if "HARDWARE" in features:
        parts.append("hw")
    if "INFINIBAND" in features:
        parts.append("ib")
    if ib_mad_dlopen:
        parts.append("ibdyn")
    if "GPU" in features:
        parts.append("nvgpu")
    if "AMD_GPU" in features:
        parts.append("amdgpu")
    if "INTEL_GPU" in features:
        parts.append("intelgpu")
    if "LUSTRE" in features:
        parts.append("lustre")
    if "BEEGFS" in features:
        parts.append("beegfs")
    if "OPA" in features:
        parts.append("opa")
    if opa_mad_dlopen:
        parts.append("opadyn")
    if cpu_backend:
        parts.append(cpu_backend)
    elif "HARDWARE" in features:
        parts.append("none")
    parts.append(tier)
    slug = "-".join(parts)
    if len(slug) > 180:
        import hashlib

        digest = hashlib.sha256(slug.encode()).hexdigest()[:16]
        slug = f"{arch}-ver{version}-{digest}-{tier}"
    return slug


def _fleet_stampede3(
    *,
    ib_mad_dlopen: bool,
    opa_mad_dlopen: bool,
    features: set[str],
) -> str | None:
    """Return 'stampede3' when compiled matrix matches fleet signature."""
    if (
        ib_mad_dlopen
        and opa_mad_dlopen
        and "INTEL_GPU" in features
        and "AMD_GPU" not in features
    ):
        return "stampede3"
    return None


def write_capability_slug_header(path: Path, slug: str) -> None:
    """Write MONITOR_CAPABILITY_SLUG for the daemon $build banner (same slug as JSON)."""
    esc = slug.replace("\\", "\\\\").replace('"', '\\"')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "/* Generated by emit_build_capabilities.py — do not edit. */\n"
        "#ifndef MONITOR_CAPABILITY_SLUG_H_\n"
        "#define MONITOR_CAPABILITY_SLUG_H_\n"
        f'#define MONITOR_CAPABILITY_SLUG "{esc}"\n'
        "#endif\n",
        encoding="utf-8",
    )


def emit_capabilities(build_dir: Path, tier: str) -> dict:
    cfg_log = _parse_config_log(build_dir)
    features, cpu_backend, debug_from_make, ib_mad_dlopen, opa_mad_dlopen, papi_hybrid = (
        _parse_src_makefile(build_dir)
    )
    debug = cfg_log.get("debug", False) or debug_from_make
    version = _package_version(build_dir)
    host_cpu = _host_cpu(build_dir)
    arch = _normalize_arch(host_cpu)
    intel_gpu = "INTEL_GPU" in features

    slug = build_capability_slug(
        arch=arch,
        version=version,
        debug=debug,
        features=features,
        cpu_backend=cpu_backend,
        tier=tier,
        ib_mad_dlopen=ib_mad_dlopen,
        opa_mad_dlopen=opa_mad_dlopen,
    )

    fleet = _fleet_stampede3(
        ib_mad_dlopen=ib_mad_dlopen,
        opa_mad_dlopen=opa_mad_dlopen,
        features=features,
    )

    doc: dict = {
        "capability_slug": slug,
        "package_version": version,
        "host_cpu": host_cpu,
        "arch": arch,
        "configure": {
            "debug": debug,
            "hardware": cfg_log.get("hardware", "HARDWARE" in features),
            "infiniband": cfg_log.get("infiniband", "INFINIBAND" in features),
            "gpu": cfg_log.get("gpu", "GPU" in features),
            "amd_gpu": cfg_log.get("amd_gpu", "AMD_GPU" in features),
            "lustre": cfg_log.get("lustre", "LUSTRE" in features),
            "beegfs": cfg_log.get("beegfs", "BEEGFS" in features),
            "opa": cfg_log.get("opa", "OPA" in features),
            "intel_gpu": intel_gpu,
            "ib_mad_dlopen": ib_mad_dlopen,
            "opa_mad_dlopen": opa_mad_dlopen,
            "cpu_backend": cpu_backend or "none",
            "papi_hybrid": papi_hybrid,
            "slow_tier": tier,
        },
        "compile_features": sorted(features),
        "build_dir": str(build_dir.resolve()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if fleet:
        doc["fleet"] = fleet
    return doc


def main() -> int:
    p = argparse.ArgumentParser(description="Emit monitor-build-capabilities.json")
    p.add_argument(
        "--build-dir",
        type=Path,
        default=Path("."),
        help="Configured Autotools build tree (e.g. .build-static)",
    )
    p.add_argument(
        "--tier",
        choices=("slowtier0", "slowtier1"),
        default="slowtier1",
        help="Runtime enable_slow_tier expectation for slug",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Output path (default: <build-dir>/{DEFAULT_OUT})",
    )
    p.add_argument(
        "--header",
        type=Path,
        default=None,
        help="Also write C header with MONITOR_CAPABILITY_SLUG (same slug)",
    )
    p.add_argument(
        "--skip-json",
        action="store_true",
        help="Skip writing monitor-build-capabilities.json (header-only)",
    )
    args = p.parse_args()

    build_dir = args.build_dir.resolve()
    if not (build_dir / "src" / "Makefile").is_file():
        print(f"emit_build_capabilities: no configured tree at {build_dir}", file=sys.stderr)
        return 1

    doc = emit_capabilities(build_dir, args.tier)
    slug = doc["capability_slug"]
    if not args.skip_json:
        out_path = args.out or (build_dir / DEFAULT_OUT)
        out_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out_path} (capability_slug={slug})")
    header_path = args.header
    if header_path is None and not args.skip_json:
        # Default: also refresh src/monitor_capability_slug.h next to the daemon build.
        header_path = build_dir / "src" / "monitor_capability_slug.h"
    if header_path is not None:
        write_capability_slug_header(header_path, slug)
        print(f"Wrote {header_path} (MONITOR_CAPABILITY_SLUG={slug})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
