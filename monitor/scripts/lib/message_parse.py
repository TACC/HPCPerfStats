"""Parse monitor message schema lines and tier markers."""
from __future__ import annotations

_SLOW_TIER_OPT = ",R=S"
_TIER_MARKERS = frozenset({"@fast", "@full"})


def schema_token_is_slow_tier(token: str) -> bool:
    parts = token.split(",")
    return any(p.strip() == "R=S" for p in parts[1:])


def schema_token_is_event_counter(token: str) -> bool:
    parts = token.split(",")
    return any(p.strip() == "E" for p in parts[1:])


def fast_schema_keys(full_events: list[str]) -> list[str]:
    return [e for e in full_events if not schema_token_is_slow_tier(e)]


def parse_schema_line(line: str) -> tuple[str, list[str]]:
    line = line.strip()
    if not line.startswith("!"):
        raise ValueError(f"not a schema line: {line!r}")
    fields = line[1:].split()
    if len(fields) < 2:
        raise ValueError(f"malformed schema line: {line!r}")
    return fields[0], fields[1:]


def parse_schema_counts(message: str) -> dict[str, list[str]]:
    counts: dict[str, list[str]] = {}
    for raw in message.splitlines():
        line = raw.strip()
        if not line or not line.startswith("!"):
            continue
        type_name, events = parse_schema_line(line)
        counts[type_name] = events
    if not counts:
        raise ValueError("no schema lines found")
    return counts


def schema_key_name(token: str) -> str:
    return token.split(",", 1)[0]
