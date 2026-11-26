"""Wire-level constants of the monitor text format and the value-token decoder.

Every constant here mirrors a definition in the monitor C sources; the drift test
``monitor/python/tests/test_wire_drift.py`` re-reads those sources and fails when they disagree:

- line prefixes: ``SF_*_CHAR`` in ``src/stats_buffer.c``
- row tier tokens: ``stats_format_row_tier_token()`` in ``src/stats_text_format.c``
- schema options: ``schema_apply_one_option()`` in ``src/schema_entry_parse.c``
- sentinel catalog: ``STATS_SENTINEL_CATALOG`` in ``src/stats_sentinel.h``
"""

from __future__ import annotations

from typing import Dict, NamedTuple, Union

PROPERTY_CHAR = "$"
SCHEMA_CHAR = "!"
MARK_CHAR = "%"

TIER_FAST = "@fast"
TIER_FULL = "@full"
TIER_TOKENS = (TIER_FAST, TIER_FULL)

NO_JOB = "-"

DEFAULT_COUNTER_WIDTH = 64


class Sentinel(NamedTuple):
    """A value the monitor could not read, emitted as ``-<code>`` instead of a number.

    Attributes:
        code: Catalog number ``n`` of the ``-n`` token (always positive here).
        slug: Upper-case catalog name (``NOT_PRESENT``), ``UNKNOWN`` for a code this
            library does not know (a newer monitor).
        description: Short human-readable reason.
        bridgeable: True when the source kept counting while the read failed, so a
            counter delta may span the gap; False starts a new counter chain.
    """

    code: int
    slug: str
    description: str
    bridgeable: bool

    def __str__(self) -> str:
        return "-%d(%s)" % (self.code, self.slug)


SENTINEL_CATALOG_VERSION = 1

SENTINELS: Dict[int, Sentinel] = {
    s.code: s
    for s in (
        Sentinel(1, "NOT_UPDATED", "not updated this cycle", True),
        Sentinel(2, "NOT_PRESENT", "counter not present", False),
        Sentinel(3, "READ_FAILED", "read failed", True),
        Sentinel(4, "PERMISSION_DENIED", "permission denied", False),
        Sentinel(5, "PMU_BUSY", "PMU busy", False),
        Sentinel(6, "NOT_SCHEDULED", "event not scheduled", False),
        Sentinel(7, "BACKEND_ERROR", "backend error", True),
        Sentinel(8, "INVALID_VALUE", "invalid value", True),
        Sentinel(9, "BACKOFF", "source in failure backoff", True),
        Sentinel(10, "NOT_BUILT", "not built into this monitor", False),
        Sentinel(11, "COUNTER_RESET", "counter reset", False),
    )
}

Value = Union[int, Sentinel]


class FormatError(ValueError):
    """A line does not follow the monitor text format (raised only in strict mode)."""


def sentinel(code: int) -> Sentinel:
    """Catalog entry for ``code``; an unknown code is never data and never bridges."""
    found = SENTINELS.get(code)
    if found is not None:
        return found
    return Sentinel(code, "UNKNOWN", "unknown monitor error code %d" % code, False)


def parse_value(token: str) -> Value:
    """Decode one value token of a sample row.

    ``"123"`` is the unsigned reading 123; ``"-3"`` is the sentinel READ_FAILED.
    Real readings are never negative, so a leading ``-`` always means a sentinel.

    Raises:
        FormatError: The token is neither an unsigned integer nor ``-<digits>``.
    """
    if token.isdigit():
        return int(token)
    if token[:1] == "-" and token[1:].isdigit():
        return sentinel(int(token[1:]))
    raise FormatError("not a value token: %r" % token)


def is_sentinel(value: object) -> bool:
    """True when ``value`` is a :class:`Sentinel` (not a reading)."""
    return isinstance(value, Sentinel)
