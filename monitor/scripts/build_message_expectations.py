#!/usr/bin/env python3
"""Build expectations_<capability_slug>.json from build capabilities + host probes."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

MONITOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MONITOR / "scripts"))

from lib.host_live_probes import default_devices_for_type, probe_host_fqdn  # noqa: E402
from lib.message_parse import (  # noqa: E402
    fast_schema_keys,
    parse_schema_counts,
    schema_key_name,
)


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
