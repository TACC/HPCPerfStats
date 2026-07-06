"""listend.py host extraction contract for schema and sample payloads."""
from __future__ import annotations


def listend_host_from_schema(body: str) -> str:
    if not body.lstrip().startswith("$"):
        raise ValueError("schema payload does not start with $")
    parts = body.split("\n")
    if len(parts) < 2:
        raise ValueError("malformed $ message: missing host line")
    host_parts = parts[1].split()
    if len(host_parts) < 2:
        raise ValueError("malformed $ message: host line missing field")
    return host_parts[1]


def listend_host_from_sample_header(header_line: str) -> str:
    fields = header_line.split()
    if len(fields) < 3:
        raise ValueError("malformed message: not enough fields to get host")
    return fields[2]


def validate_schema_listend_contract(body: str, manifest: dict) -> None:
    host = listend_host_from_schema(body)
    want = manifest.get("host_fqdn") or ""
    if want and host != want:
        raise ValueError(f"schema $hostname host {host!r} != manifest {want!r}")

    ver = manifest.get("program_version", "")
    if ver:
        for line in body.splitlines():
            s = line.strip()
            if s.startswith("$hpcperfstats"):
                parts = s.split()
                if len(parts) >= 2 and parts[1] != ver:
                    raise ValueError(f"schema version {parts[1]!r} != manifest {ver!r}")
                break
