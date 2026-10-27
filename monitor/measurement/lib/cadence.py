"""Parse hpcperfstatsd site conf cadence keys."""

from __future__ import annotations

import re
from typing import Any

_KEY_RE = re.compile(
    r"^(?P<key>sample_freq|sample_freq_slow|send_freq|enable_slow_tier|freq)"
    r"(?:\s+|:|=)\s*(?P<value>\S+)",
    re.IGNORECASE,
)

CANONICAL_KEYS = (
    "sample_freq",
    "sample_freq_slow",
    "send_freq",
    "enable_slow_tier",
)

UPSTREAM_DEFAULTS = {
    "sample_freq": 30.0,
    "sample_freq_slow": 600.0,
    "send_freq": 300.0,
    "enable_slow_tier": 1,
}


def parse_monitor_conf(text: str) -> dict[str, Any]:
    """Extract cadence keys from an hpcperfstats.conf body.

    Last assignment wins. ``freq`` is recorded as a deprecated alias of
    ``sample_freq`` only when ``sample_freq`` is absent.
    """
    found: dict[str, str] = {}
    alias_freq: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = _KEY_RE.match(line)
        if match is None:
            continue
        key = match.group("key").lower()
        value = match.group("value")
        if key == "freq":
            alias_freq = value
            continue
        found[key] = value
    if "sample_freq" not in found and alias_freq is not None:
        found["sample_freq"] = alias_freq
        found["sample_freq_alias"] = "freq"
    numeric: dict[str, Any] = {}
    for key in CANONICAL_KEYS:
        raw_value = found.get(key)
        if raw_value is None:
            numeric[key] = None
            continue
        try:
            if key == "enable_slow_tier":
                numeric[key] = int(float(raw_value))
            else:
                numeric[key] = float(raw_value)
        except ValueError:
            numeric[key] = raw_value
    numeric["raw"] = found
    numeric["missing_keys"] = [k for k in CANONICAL_KEYS if k not in found]
    numeric["matches_upstream_defaults"] = all(
        numeric.get(k) == UPSTREAM_DEFAULTS[k] for k in CANONICAL_KEYS if numeric.get(k) is not None
    ) and not numeric["missing_keys"]
    return numeric
