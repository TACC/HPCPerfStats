"""``!`` schema lines: the ordered key list (and key options) of one stats type."""

from __future__ import annotations

import re
from typing import List, NamedTuple, Optional, Tuple

from .wire import DEFAULT_COUNTER_WIDTH, SCHEMA_CHAR, FormatError

_UNIT_RE = re.compile(r"^(\d+)?(.*)$")


class SchemaEntry(NamedTuple):
    """One key of a ``!`` schema line, such as ``rd_sectors,E,U=512B,R=S``.

    Attributes:
        key: Metric name (``rd_sectors``).
        token: The raw schema token as emitted.
        is_event: ``E`` flag: a monotonically increasing counter. Subtract two
            readings (see :func:`hpcperfstats_raw.counters.counter_delta`).
            Without ``E`` the value is a gauge: an instantaneous level.
        is_control: ``C`` flag: a control/configuration value (no collector sets it
            today; kept for completeness of the grammar).
        unit: Unit text after ``U=`` without its leading multiplier, or None.
        multiplier: Leading integer of ``U=`` (``U=512B`` -> 512), else 1. Multiply the
            value by it to get ``unit`` (512-byte sectors -> bytes).
        width: Counter width in bits from ``W=`` (wraps modulo ``2**width``), else 64.
        slow: ``R=S`` flag: slow-tier key, present only in ``@full`` rows (and legacy
            rows) when the daemon runs two-tier collection.
    """

    key: str
    token: str
    is_event: bool
    is_control: bool
    unit: Optional[str]
    multiplier: int
    width: int
    slow: bool

    @property
    def is_gauge(self) -> bool:
        """True for an instantaneous level (no ``E`` flag)."""
        return not self.is_event


def parse_schema_entry(token: str) -> SchemaEntry:
    """Parse one schema token ``key[,opt[=arg]]...`` exactly as the daemon does.

    Option letters are case-insensitive; unknown options are ignored, as in
    ``schema_apply_one_option()``. ``R=`` other than ``S`` means fast tier.
    """
    parts = token.split(",")
    key = parts[0]
    if not key:
        raise FormatError("empty schema key in %r" % token)
    is_event = is_control = slow = False
    unit: Optional[str] = None
    multiplier = 1
    width = DEFAULT_COUNTER_WIDTH
    for opt in parts[1:]:
        if not opt:
            continue
        name, _, arg = opt.partition("=")
        letter = name[:1].upper()
        if letter == "E":
            is_event = True
        elif letter == "C":
            is_control = True
        elif letter == "U" and arg:
            m = _UNIT_RE.match(arg)
            assert m is not None
            if m.group(1) and m.group(2):
                multiplier, unit = int(m.group(1)), m.group(2)
            else:
                unit = arg
        elif letter == "W" and arg:
            width = int(arg, 0)
        elif letter == "R":
            slow = arg[:1].upper() == "S"
    return SchemaEntry(key, token, is_event, is_control, unit, multiplier, width, slow)


class TypeSchema(NamedTuple):
    """Ordered keys of one stats type, from one ``!<type> ...`` line.

    Attributes:
        type: Stats type name (``host_cpu``).
        entries: Every key in emit order (the order of values in ``@full`` and
            legacy rows).
    """

    type: str
    entries: Tuple[SchemaEntry, ...]

    @property
    def keys(self) -> List[str]:
        """All key names in ``@full`` / legacy row order."""
        return [e.key for e in self.entries]

    @property
    def fast_entries(self) -> Tuple[SchemaEntry, ...]:
        """Keys carried by ``@fast`` rows: entries without ``R=S``, in schema order."""
        return tuple(e for e in self.entries if not e.slow)

    def entry(self, key: str) -> SchemaEntry:
        """Schema entry for ``key`` (KeyError when the type has no such key)."""
        for e in self.entries:
            if e.key == key:
                return e
        raise KeyError(key)


def parse_schema_line(line: str) -> TypeSchema:
    """Parse ``!<type> <entry> <entry> ...`` into a :class:`TypeSchema`."""
    s = line.strip()
    if not s.startswith(SCHEMA_CHAR) or len(s) < 2:
        raise FormatError("not a schema line: %r" % line)
    fields = s[1:].split()
    return TypeSchema(fields[0], tuple(parse_schema_entry(t) for t in fields[1:]))
