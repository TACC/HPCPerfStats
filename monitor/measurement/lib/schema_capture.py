"""Schema keys per collector type from payloads drained off ``<queue>_measure``.

NONE / SOLO / VARIANT / CADENCE legs publish to the measure queue; the daemon sends a ``$``
schema message (``!<type> key,flags ...`` lines) at start. The harness drains the queue after
each such leg and keeps the last schema message as ``raw/legs/<id>/schema.txt``, so a run
records which keys every type emitted and two runs can be diffed (e.g. NVLink keys under
``nvidia_gpu`` unchanged across a DCGM watch change). Python 3.9 compatible (runs on nodes).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def last_schema_message(messages: Iterable[str]) -> str | None:
    """The last payload that is a schema message (starts with ``$``), or None."""
    found = None
    for text in messages:
        if text.lstrip().startswith("$"):
            found = text
    return found


def parse_schema_keys(text: str) -> dict[str, list[str]]:
    """``{type: [key, ...]}`` from a schema message's ``!type key,flags ...`` lines."""
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.startswith("!"):
            continue
        tokens = line[1:].split()
        if not tokens:
            continue
        out[tokens[0]] = [tok.split(",", 1)[0] for tok in tokens[1:] if tok.split(",", 1)[0]]
    return out


def merge_leg_keys(per_leg: Iterable[dict[str, list[str]]]) -> dict[str, list[str]]:
    """Union over legs; a type seen in several legs keeps the last leg's key list."""
    out: dict[str, list[str]] = {}
    for keys in per_leg:
        out.update(keys)
    return dict(sorted(out.items()))


def diff_keys(base: dict[str, list[str]], run: dict[str, list[str]]) -> dict[str, Any]:
    """Per-type ``{added, removed}`` plus types present in only one run."""
    types = {}
    for name in sorted(set(base) & set(run)):
        added = [k for k in run[name] if k not in base[name]]
        removed = [k for k in base[name] if k not in run[name]]
        if added or removed:
            types[name] = {"added": added, "removed": removed}
    return {"changed": types, "only_in_base": sorted(set(base) - set(run)),
            "only_in_run": sorted(set(run) - set(base))}
