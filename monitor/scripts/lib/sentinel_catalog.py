"""Monitor value sentinel catalog for the shm validators, read from ``src/stats_sentinel.h``.

A key the daemon could not read is emitted as the token ``-<n>`` (``n`` a catalog code) instead of
its value. Bridgeable codes mean the source counter kept running while the read failed, so the
next valid value must be >= the last one (mod 2^W); any other code breaks the counter chain.
"""
from __future__ import annotations

import re
from pathlib import Path

HEADER = Path(__file__).resolve().parents[2] / "src" / "stats_sentinel.h"

_RE_ENTRY = re.compile(r'X\((\d+),\s*([A-Z_]+),\s*"[^"]*"\)')
_RE_VERSION = re.compile(r"#define\s+STATS_SENTINEL_CATALOG_VERSION\s+(\d+)")

# Codes whose gap a counter delta may span (consumer ``SentinelCode.bridge``).
BRIDGEABLE_SLUGS = frozenset(
    {"NOT_UPDATED", "READ_FAILED", "BACKEND_ERROR", "INVALID_VALUE", "BACKOFF"}
)


def _load(header: Path) -> tuple[int, dict[int, str]]:
    text = header.read_text(encoding="utf-8")
    version = _RE_VERSION.search(text)
    codes = {int(n): slug for n, slug in _RE_ENTRY.findall(text)}
    if version is None or not codes:
        raise RuntimeError(f"sentinel catalog not found in {header}")
    return int(version.group(1)), codes


CATALOG_VERSION, SENTINEL_SLUGS = _load(HEADER)
BRIDGEABLE_CODES = frozenset(n for n, slug in SENTINEL_SLUGS.items() if slug in BRIDGEABLE_SLUGS)


def sentinel_slug(code: int) -> str:
    """Catalog slug for ``code`` (the n of the ``-n`` token), ``UNKNOWN<n>`` otherwise."""
    return SENTINEL_SLUGS.get(code, f"UNKNOWN{code}")


def is_bridgeable(code: int) -> bool:
    """True for a catalog code whose gap a counter may bridge; unknown codes break the chain."""
    return code in BRIDGEABLE_CODES


def sentinels_banner_version(schema_body: str | None) -> int | None:
    """Version from the ``$sentinels <v>`` banner line, or None (knob 0 or an older daemon)."""
    for line in (schema_body or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "$sentinels" and parts[1].isdigit():
            return int(parts[1])
    return None
