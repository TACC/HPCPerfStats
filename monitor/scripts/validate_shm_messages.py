#!/usr/bin/env python3
"""Validate DEBUG shm monitor payloads against expectations manifest."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MONITOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MONITOR / "scripts"))

from lib.device_validate import validate_devices_in_payload  # noqa: E402
from lib.golden_diff import compare_golden_shm  # noqa: E402
from lib.listend_contract import (  # noqa: E402
    listend_host_from_sample_header,
    validate_schema_listend_contract,
)
from lib.live_spot_check import run_live_spot_checks  # noqa: E402
from lib.message_parse import parse_schema_counts  # noqa: E402
from lib.row_validate import (  # noqa: E402
    validate_sample_header,
    validate_sample_payload,
    validate_schema_tail_rows,
)
from lib.value_plausibility import check_plausibility  # noqa: E402


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


def wait_for_shm(shm_dir: Path, *, enable_slow: bool, timeout_sec: float) -> None:
    needed = {"schema", "full"}
    if enable_slow:
        needed.add("fast")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if all((shm_dir / name).is_file() for name in needed):
            return
        time.sleep(0.25)
    missing = sorted(name for name in needed if not (shm_dir / name).is_file())
    raise TimeoutError(f"shm files not ready after {timeout_sec}s: {missing}")


def validate_schema_file(path: Path, schema_by_type: dict[str, list[str]], manifest: dict) -> str:
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
    validate_schema_listend_contract(body, manifest)
    enable_slow = manifest.get("enable_slow_tier", True)
    if enable_slow:
        validate_schema_tail_rows(body, schema_by_type)
        validate_devices_in_payload(
            body,
            manifest,
            schema_by_type,
            require_tier=True,
            allowed_tier="@full",
        )
    else:
        validate_sample_payload(body, schema_by_type, require_tier=False, allowed_tier=None)
        validate_devices_in_payload(
            body,
            manifest,
            schema_by_type,
            require_tier=False,
            allowed_tier=None,
        )
    return body


def validate_sample_file(
    path: Path,
    kind: str,
    schema_by_type: dict[str, list[str]],
    manifest: dict,
) -> str:
    body = read_strict_utf8(path)
    if not body.strip():
        raise ValueError(f"{path}: empty")
    enable_slow = manifest.get("enable_slow_tier", True)
    if enable_slow:
        allowed = "@fast" if kind == "fast" else "@full"
        validate_sample_payload(
            body,
            schema_by_type,
            require_tier=True,
            allowed_tier=allowed,
        )
        validate_devices_in_payload(
            body,
            manifest,
            schema_by_type,
            require_tier=True,
            allowed_tier=allowed,
        )
    else:
        validate_sample_payload(body, schema_by_type, require_tier=False, allowed_tier=None)
        validate_devices_in_payload(
            body,
            manifest,
            schema_by_type,
            require_tier=False,
            allowed_tier=None,
        )
    return body


def validate_sample_hosts(manifest: dict, bodies: dict[str, str], errors: list[str]) -> None:
    want = manifest.get("host_fqdn") or ""
    if not want:
        return
    for kind, body in bodies.items():
        if not body:
            continue
        first = next((ln for ln in body.splitlines() if ln.strip()), "")
        if not first.lstrip()[0:1].isdigit():
            continue
        try:
            host = listend_host_from_sample_header(first.lstrip())
        except ValueError as exc:
            errors.append(f"FAIL {kind} listend host: {exc}")
            continue
        if host != want:
            errors.append(f"FAIL {kind}: host {host!r} != manifest {want!r}")


def main() -> int:
    p = argparse.ArgumentParser(description="Validate shm monitor messages")
    p.add_argument("--capabilities", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--shm-dir", type=Path, required=True)
    p.add_argument("--report", type=Path, default=None)
    p.add_argument(
        "--fixture-dir",
        type=Path,
        default=None,
        help="Use fixture copy instead of live shm (for CI synthetic)",
    )
    p.add_argument(
        "--live-spot-check",
        action="store_true",
        default=None,
        help="Re-read /proc,/sys and compare emitted values (default on live shm)",
    )
    p.add_argument("--no-live-spot-check", action="store_true")
    p.add_argument(
        "--strict-live-spot-check",
        action="store_true",
        help="Treat live spot-check warnings as errors",
    )
    p.add_argument(
        "--strict-plausibility",
        action="store_true",
        help="Treat plausibility warnings as errors",
    )
    p.add_argument(
        "--no-freshness",
        action="store_true",
        help="Skip sample timestamp freshness checks",
    )
    p.add_argument(
        "--golden-dir",
        type=Path,
        default=None,
        help="Optional byte diff vs shm_*_<slug>.txt goldens",
    )
    p.add_argument(
        "--spot-check-max-age",
        type=float,
        default=120.0,
        help="Max sample age (sec) for monotonic counter spot checks",
    )
    p.add_argument(
        "--wait-shm-seconds",
        type=float,
        default=0.0,
        help="Poll shm-dir until schema/fast/full exist (live verify)",
    )
    args = p.parse_args()

    caps = load_json(args.capabilities)
    manifest = load_json(args.manifest)
    capability_gate(caps, manifest)

    is_fixture = args.fixture_dir is not None
    shm_dir = args.fixture_dir if is_fixture else args.shm_dir
    schema_by_type = schema_keys_from_manifest(manifest)
    enable_slow = manifest.get("enable_slow_tier", True)

    if args.wait_shm_seconds > 0 and not is_fixture:
        try:
            wait_for_shm(shm_dir, enable_slow=enable_slow, timeout_sec=args.wait_shm_seconds)
        except TimeoutError as exc:
            print(f"FAIL wait_shm: {exc}", file=sys.stderr)
            return 1

    do_live_spot = args.live_spot_check
    if do_live_spot is None:
        do_live_spot = not is_fixture
    if args.no_live_spot_check:
        do_live_spot = False

    errors: list[str] = []
    warnings: list[str] = []
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

    schema_body: str | None = None
    fast_body: str | None = None
    full_body: str | None = None

    if schema_path.is_file():
        def _schema():
            nonlocal schema_body
            schema_body = validate_schema_file(schema_path, schema_by_type, manifest)

        run("schema", _schema)
    else:
        errors.append("FAIL schema: missing file")

    if enable_slow:
        if fast_path.is_file():
            def _fast():
                nonlocal fast_body
                fast_body = validate_sample_file(fast_path, "fast", schema_by_type, manifest)

            run("fast", _fast)
        else:
            errors.append("FAIL fast: missing file")
    if full_path.is_file():
        def _full():
            nonlocal full_body
            full_body = validate_sample_file(full_path, "full", schema_by_type, manifest)

        run("full", _full)
    else:
        errors.append("FAIL full: missing file")

    sample_bodies = {}
    if fast_body:
        sample_bodies["fast"] = fast_body
    if full_body:
        sample_bodies["full"] = full_body
    validate_sample_hosts(manifest, sample_bodies, errors)

    if not errors:
        p_notes, p_warn, p_err = check_plausibility(
            manifest,
            schema_by_type,
            full_body=full_body,
            fast_body=fast_body,
            schema_body=schema_body,
            no_freshness=args.no_freshness or is_fixture,
            strict=args.strict_plausibility,
        )
        notes.extend(p_notes)
        warnings.extend(p_warn)
        errors.extend(p_err)

    if not errors and do_live_spot and full_body:
        ls_notes, ls_warn, ls_err = run_live_spot_checks(
            manifest,
            schema_by_type,
            full_body=full_body,
            max_age_sec=args.spot_check_max_age,
            strict=args.strict_live_spot_check,
        )
        notes.extend(ls_notes)
        warnings.extend(ls_warn)
        errors.extend(ls_err)
    elif do_live_spot and not full_body:
        warnings.append("WARN live_spot skipped: no full payload")

    if args.golden_dir and not errors:
        slug = manifest.get("capability_slug", "")
        golden_errors = compare_golden_shm(
            shm_dir,
            args.golden_dir,
            slug,
            enable_slow_tier=enable_slow,
        )
        if golden_errors:
            errors.extend(golden_errors)
        else:
            notes.append("PASS golden diff")

    report_lines = notes + warnings + errors
    report_text = "\n".join(report_lines) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report_text, encoding="utf-8")
    else:
        print(report_text, end="")

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    check_count = len(notes)
    print(f"validate_shm_messages: OK ({check_count} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
