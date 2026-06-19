#!/usr/bin/env python3
"""Validate DEBUG shm monitor payloads against expectations manifest."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

MONITOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MONITOR / "scripts"))

from lib.message_parse import parse_schema_counts  # noqa: E402
from lib.row_validate import (  # noqa: E402
    validate_sample_header,
    validate_sample_payload,
    validate_schema_tail_rows,
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_strict_utf8(path: Path) -> str:
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path}: not strict UTF-8") from exc


def capability_gate(caps: dict, manifest: dict) -> None:
    live = caps.get("capability_slug")
    want = manifest.get("capability_slug")
    if live != want:
        raise SystemExit(
            f"capability slug mismatch: manifest={want!r} live={live!r}"
        )


def schema_keys_from_manifest(manifest: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for type_name, info in manifest.get("types", {}).items():
        out[type_name] = info.get("schema_keys", [])
    if not out:
        raise ValueError("manifest has no types")
    return out


def validate_schema_file(path: Path, schema_by_type: dict[str, list[str]], manifest: dict) -> None:
    body = read_strict_utf8(path)
    if not body.strip():
        raise ValueError(f"{path}: empty")
    if not body.lstrip().startswith("$"):
        raise ValueError(f"{path}: does not start with $")
    parsed = parse_schema_counts(body)
    for type_name, info in manifest.get("types", {}).items():
        keys = info.get("schema_keys", [])
        if type_name not in parsed:
            raise ValueError(f"manifest type {type_name!r} missing from schema file")
        if parsed[type_name] != keys:
            raise ValueError(f"schema keys mismatch for type {type_name!r}")
    enable_slow = manifest.get("enable_slow_tier", True)
    if enable_slow:
        validate_schema_tail_rows(body, schema_by_type)
    else:
        validate_sample_payload(body, schema_by_type, require_tier=False, allowed_tier=None)


def validate_sample_file(
    path: Path,
    kind: str,
    schema_by_type: dict[str, list[str]],
    manifest: dict,
) -> int:
    body = read_strict_utf8(path)
    if not body.strip():
        raise ValueError(f"{path}: empty")
    enable_slow = manifest.get("enable_slow_tier", True)
    if enable_slow:
        allowed = "@fast" if kind == "fast" else "@full"
        return validate_sample_payload(
            body,
            schema_by_type,
            require_tier=True,
            allowed_tier=allowed,
        )
    return validate_sample_payload(
        body,
        schema_by_type,
        require_tier=False,
        allowed_tier=None,
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Validate shm monitor messages")
    p.add_argument("--capabilities", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--shm-dir", type=Path, required=True)
    p.add_argument("--report", type=Path, default=None)
    p.add_argument("--fixture-dir", type=Path, default=None,
                   help="Use fixture copy instead of live shm (for CI synthetic)")
    args = p.parse_args()

    caps = load_json(args.capabilities)
    manifest = load_json(args.manifest)
    capability_gate(caps, manifest)

    shm_dir = args.fixture_dir if args.fixture_dir else args.shm_dir
    schema_by_type = schema_keys_from_manifest(manifest)
    enable_slow = manifest.get("enable_slow_tier", True)

    errors: list[str] = []
    notes: list[str] = []

    def run(name: str, fn):
        try:
            fn()
            notes.append(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"FAIL {name}: {exc}")

    schema_path = shm_dir / "schema"
    fast_path = shm_dir / "fast"
    full_path = shm_dir / "full"

    if schema_path.is_file():
        run("schema", lambda: validate_schema_file(schema_path, schema_by_type, manifest))
    else:
        errors.append("FAIL schema: missing file")

    if enable_slow:
        if fast_path.is_file():
            run(
                "fast",
                lambda: validate_sample_file(fast_path, "fast", schema_by_type, manifest),
            )
        else:
            errors.append("FAIL fast: missing file")
    if full_path.is_file():
        run(
            "full",
            lambda: validate_sample_file(full_path, "full", schema_by_type, manifest),
        )
    else:
        errors.append("FAIL full: missing file")

    host = manifest.get("host_fqdn", "")
    for kind in ("fast", "full"):
        path = shm_dir / kind
        if not path.is_file():
            continue
        body = read_strict_utf8(path)
        first = next((ln for ln in body.splitlines() if ln.strip()), "")
        if first.lstrip()[0:1].isdigit():
            _, _, h = validate_sample_header(first.lstrip())
            if host and h != host:
                errors.append(f"FAIL {kind}: host {h!r} != manifest {host!r}")

    report_lines = notes + errors
    report_text = "\n".join(report_lines) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report_text, encoding="utf-8")
    else:
        print(report_text, end="")

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"validate_shm_messages: OK ({len(notes)} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
