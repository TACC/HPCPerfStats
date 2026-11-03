"""Parse the daemon's startup ``Enabled types:`` line (monitor.c release startup summary).

This line is the ground truth for which collectors actually run on a node: it reflects
lspci auto-disables and init failures that monitor-build-capabilities.json cannot.
"""

from __future__ import annotations

import re

_ENABLED_RE = re.compile(r"Enabled types:\s*(.*?)\s*$")


def all_enabled_types(text: str) -> list[list[str]]:
    """Every ``Enabled types:`` line in order; ``(none)`` gives an empty list."""
    out: list[list[str]] = []
    for line in text.splitlines():
        m = _ENABLED_RE.search(line)
        if not m:
            continue
        body = m.group(1)
        if body in ("", "(none)"):
            out.append([])
        else:
            out.append([t.strip() for t in body.split(",") if t.strip()])
    return out


def last_enabled_types(text: str) -> list[str] | None:
    """Types from the most recent startup; None when no such line was logged."""
    found = all_enabled_types(text)
    return found[-1] if found else None
