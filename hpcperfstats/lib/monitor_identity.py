"""
Per-host monitor signature persisted in Redis on ``$`` schema rotation.

listend parses ``$hpcperfstats`` / ``$uname`` / optional ``$build`` and ``!``
schema type names from the monitor banner, then SETs
``monitor_identity:{fqdn}``. Admin Monitor telemetry health joins those keys
for sampled hosts. Missing ``$build`` (old RPMs) is tolerated.

Attributes:
  MONITOR_IDENTITY_KEY_PREFIX: Redis key prefix before the FQDN.
  SCHEMA_TYPES_CAP: Max ``!`` type names stored per identity document.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import json
import time

MONITOR_IDENTITY_KEY_PREFIX = "monitor_identity:"
SCHEMA_TYPES_CAP = 200


def monitor_identity_redis_key(fqdn: str) -> str:
    """
    Return the Redis key for a host's monitor identity document.

    Args:
      fqdn (str): Fully-qualified hostname (must contain a ``.``).

    Returns:
      str: ``monitor_identity:{fqdn}`` key string.

    Examples:
      >>> monitor_identity_redis_key("c101-001.cluster.example")
      'monitor_identity:c101-001.cluster.example'
    """
    return f"{MONITOR_IDENTITY_KEY_PREFIX}{fqdn}"


def parse_monitor_identity_from_dollar_message(
    message: str,
    *,
    updated_at: int | None = None,
    schema_types_cap: int | None = None,
) -> dict[str, Any] | None:
    """
    Parse a monitor ``$`` schema-rotation payload into an identity mapping.

    Expects the listend ``$`` shape: first line starts with ``$``, second line
    is ``1 <fqdn>``, then property lines (``$hpcperfstats``, ``$uname``,
    optional ``$build``) and ``!type …`` schema lines. Old RPMs without
    ``$build`` still yield version / uname / schema_types.

    Args:
      message (str): Full ``$`` rotation payload text.
      updated_at (int | None): Unix epoch for ``updated_at``; defaults to now.
      schema_types_cap (int | None): Max schema type names; defaults to
        ``SCHEMA_TYPES_CAP``.

    Returns:
      dict[str, Any] | None: Identity document with ``fqdn``,
      ``package_version``, ``uname``, ``capability_slug`` (str or None),
      ``schema_types``, and ``updated_at``; or ``None`` when the payload is
      not a parseable ``$`` message with an FQDN host.

    Examples:
      >>> body = (
      ...     "$\\n1 node1.example.com\\n"
      ...     "$hpcperfstats 3.0\\n"
      ...     "$uname Linux aarch64 6.1.0 #1\\n"
      ...     "$build arch_aarch64_ver_3.0\\n"
      ...     "!host_cpu user,E system,E\\n"
      ... )
      >>> ident = parse_monitor_identity_from_dollar_message(
      ...     body, updated_at=1_700_000_000)
      >>> ident["fqdn"]
      'node1.example.com'
      >>> ident["package_version"]
      '3.0'
      >>> ident["capability_slug"]
      'arch_aarch64_ver_3.0'
      >>> ident["schema_types"]
      ['host_cpu']
    """
    if not message or message[0] != "$":
        return None
    lines = message.split("\n")
    if len(lines) < 2:
        return None
    host_parts = lines[1].split()
    if len(host_parts) < 2:
        return None
    fqdn = host_parts[1].strip()
    if not fqdn or "." not in fqdn:
        return None

    package_version: str | None = None
    uname: str | None = None
    capability_slug: str | None = None
    schema_types: list[str] = []
    seen_types: set[str] = set()
    cap = (
        SCHEMA_TYPES_CAP
        if schema_types_cap is None
        else max(0, int(schema_types_cap))
    )

    for raw in lines[2:]:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("$hpcperfstats"):
            parts = line.split(None, 1)
            if len(parts) >= 2:
                package_version = parts[1].strip() or None
            continue
        if line.startswith("$uname"):
            parts = line.split(None, 1)
            if len(parts) >= 2:
                uname = parts[1].strip() or None
            continue
        if line.startswith("$build"):
            parts = line.split(None, 1)
            if len(parts) >= 2:
                capability_slug = parts[1].strip() or None
            continue
        if line.startswith("!"):
            fields = line[1:].split()
            if not fields:
                continue
            type_name = fields[0].strip()
            if (
                type_name
                and type_name not in seen_types
                and len(schema_types) < cap
            ):
                seen_types.add(type_name)
                schema_types.append(type_name)

    when = int(updated_at if updated_at is not None else time.time())
    return {
        "fqdn": fqdn,
        "package_version": package_version,
        "uname": uname,
        "capability_slug": capability_slug,
        "schema_types": schema_types,
        "updated_at": when,
    }


def set_monitor_identity(
    redis_client: Any,
    identity: Mapping[str, Any],
    *,
    ttl_seconds: int,
) -> None:
    """
    Best-effort SET of ``monitor_identity:{fqdn}`` JSON with a TTL.

    Args:
      redis_client (Any): redis-py client with ``set(name, value, ex=…)``.
      identity (Mapping[str, Any]): Document from
        ``parse_monitor_identity_from_dollar_message`` (must include ``fqdn``).
      ttl_seconds (int): Redis key TTL in seconds (aligned with
        ``recent_host``).

    Returns:
      None

    Examples:
      >>> class _R:
      ...     def set(self, name, value, ex=None):
      ...         self.last = (name, value, ex)
      >>> r = _R()
      >>> set_monitor_identity(
      ...     r,
      ...     {"fqdn": "a.example.com", "package_version": "1.0",
      ...      "uname": None, "capability_slug": None,
      ...      "schema_types": [], "updated_at": 1},
      ...     ttl_seconds=60,
      ... )
      >>> r.last[0]
      'monitor_identity:a.example.com'
    """
    fqdn = str(identity.get("fqdn") or "")
    if not fqdn or "." not in fqdn:
        return
    if redis_client is None:
        return
    payload = {
        "fqdn": fqdn,
        "package_version": identity.get("package_version"),
        "uname": identity.get("uname"),
        "capability_slug": identity.get("capability_slug"),
        "schema_types": list(identity.get("schema_types") or []),
        "updated_at": int(identity.get("updated_at") or time.time()),
    }
    redis_client.set(
        monitor_identity_redis_key(fqdn),
        json.dumps(payload, separators=(",", ":"), sort_keys=True),
        ex=max(1, int(ttl_seconds)),
    )


def load_monitor_identities_for_hosts(
    redis_client: Any,
    fqdns: Sequence[str],
) -> list[dict[str, Any]]:
    """
    Load Redis ``monitor_identity:*`` JSON documents for the given FQDNs.

    Missing keys and malformed JSON are skipped (callers may emit
    ``signature_absent`` findings for sampled hosts with no document).

    Args:
      redis_client (Any): redis-py client supporting ``get``, or ``None``.
      fqdns (Sequence[str]): Hostnames to look up.

    Returns:
      list[dict[str, Any]]: Parsed identity documents in ``fqdns`` order when
      present.

    Examples:
      >>> class _R:
      ...     def get(self, name):
      ...         return None
      >>> load_monitor_identities_for_hosts(_R(), ["a.example.com"])
      []
    """
    if redis_client is None:
        return []
    out: list[dict[str, Any]] = []
    for fqdn in fqdns:
        host = str(fqdn or "")
        if not host or "." not in host:
            continue
        try:
            raw = redis_client.get(monitor_identity_redis_key(host))
        except Exception:
            continue
        if raw is None:
            continue
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            doc = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        doc.setdefault("fqdn", host)
        out.append(doc)
    return out
