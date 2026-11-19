"""
Monitor value sentinel catalog (mirror of ``monitor/src/stats_sentinel.h``).

The monitor emits the token ``-<n>`` in place of a value it could not read;
each code names one reason. Real values are unsigned, so any negative value in
``host_data.value`` is a sentinel, never data. The drift test
``test_monitor_sentinel_catalog_drift`` keeps codes, slugs and descriptions
here equal to the C header.

The ``bridge`` and ``kind`` attributes are consumer-only:

- ``bridge``: the source counter kept running while the monitor failed to read
  it, so a delta may span the gap. Otherwise the next valid sample starts a new
  chain (delta NaN).
- ``kind``: ``static`` codes mean the metric is unavailable on this host;
  ``transient`` codes are coverage gaps.

Attributes:
  CATALOG_VERSION (int): Matches ``STATS_SENTINEL_CATALOG_VERSION``.
  CATALOG (dict[int, SentinelCode]): Known codes keyed by negative wire code.
  RESEARCHER_VISIBLE_CODES (frozenset[int]): Codes whose reason non-staff
    users see on Job Detail.
  _LIKWID_UNPROVEN_BRIDGE_TYPES (frozenset[str]): Types whose bridge is not
    proven on a monitor 3.0 LIKWID build (``likwid`` slug without
    ``x86perf``); 3.1 x86 builds carry ``x86perf`` and bridge.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CATALOG_VERSION = 1


@dataclass(frozen=True)
class SentinelCode:
  """
  One catalog entry: a negative wire code and how the consumer treats it.

  Attributes:
    code (int): Negative wire code (``-n``).
    slug (str): Upper-case name shared with the C enum
      ``STATS_SENTINEL_<slug>``.
    description (str): Short human-readable reason used in metric reasons.
    bridge (bool): True when a counter delta may span a gap of this code.
    kind (str): ``"static"`` or ``"transient"``.

  Examples:
    >>> SentinelCode(-3, "READ_FAILED", "read failed", True, "transient").bridge
    True
  """

  code: int
  slug: str
  description: str
  bridge: bool
  kind: str


CATALOG: dict[int, SentinelCode] = {
  e.code: e
  for e in (
    SentinelCode(
      -1, "NOT_UPDATED", "not updated this cycle", True, "transient"
    ),
    SentinelCode(-2, "NOT_PRESENT", "counter not present", False, "static"),
    SentinelCode(-3, "READ_FAILED", "read failed", True, "transient"),
    SentinelCode(-4, "PERMISSION_DENIED", "permission denied", False, "static"),
    SentinelCode(-5, "PMU_BUSY", "PMU busy", False, "transient"),
    SentinelCode(
      -6, "NOT_SCHEDULED", "event not scheduled", False, "transient"
    ),
    SentinelCode(-7, "BACKEND_ERROR", "backend error", True, "transient"),
    SentinelCode(-8, "INVALID_VALUE", "invalid value", True, "transient"),
    SentinelCode(-9, "BACKOFF", "source in failure backoff", True, "transient"),
    SentinelCode(
      -10, "NOT_BUILT", "not built into this monitor", False, "static"
    ),
    SentinelCode(-11, "COUNTER_RESET", "counter reset", False, "transient"),
  )
}

RESEARCHER_VISIBLE_CODES: frozenset[int] = frozenset({-2})

_LIKWID_UNPROVEN_BRIDGE_TYPES = frozenset({"host_cpu_hw"})


def lookup(code: int) -> SentinelCode:
  """
  Return the catalog entry for a negative wire code.

  A newer monitor may emit a code this consumer does not know. It is still not
  data: it maps to an ``UNKNOWN`` entry that is transient and never bridged.

  Args:
    code (int): Negative sentinel code (for example ``-2``).

  Returns:
    SentinelCode: The catalog entry, or an ``UNKNOWN`` entry carrying ``code``.

  Raises:
    ValueError: When ``code`` is not negative (0 and above are real values).

  Examples:
    >>> lookup(-2).slug
    'NOT_PRESENT'
    >>> lookup(-200).slug
    'UNKNOWN'
  """
  if code >= 0:
    raise ValueError(f"sentinel codes are negative, got {code}")
  entry = CATALOG.get(code)
  if entry is not None:
    return entry
  return SentinelCode(
    code, "UNKNOWN", "unknown monitor error", False, "transient"
  )


def bridge_proven(typename: str, build_slug: str | None) -> bool:
  """
  Report whether bridgeable codes may bridge for ``typename`` on this build.

  Monitor 3.1 x86 builds read core counters from perf_event directly and
  carry the ``x86perf`` slug token (never ``likwid``), so ``host_cpu_hw``
  bridges there. Monitor 3.0 x86 builds published LIKWID's counter values,
  whose behavior across a failed read is unverified, so a ``likwid`` slug
  without ``x86perf`` (3.0 hosts and archived history) keeps ``host_cpu_hw``
  unbridged. A missing slug (old daemon) is treated as LIKWID without
  ``x86perf``. Slug tokens are compared exactly after splitting on ``-``.

  Args:
    typename (str): Monitor type name (for example ``host_cpu_hw``).
    build_slug (str | None): ``$build`` capability slug, or None when the
      stream carried no ``$build`` line.

  Returns:
    bool: False only for an unproven type on a LIKWID build (or unknown
    build) without ``x86perf``; True otherwise.

  Examples:
    >>> bridge_proven("host_cpu_hw", "x86_64-ver3-hw-likwid-full")
    False
    >>> bridge_proven("host_cpu_hw", "x86_64-ver3.1-hw-ib-x86perf-sent1")
    True
    >>> bridge_proven("host_ib", None)
    True
  """
  if typename not in _LIKWID_UNPROVEN_BRIDGE_TYPES:
    return True
  if build_slug is None:
    return False
  tokens = set(build_slug.split("-"))
  return "likwid" not in tokens or "x86perf" in tokens


def is_bridgeable(code: int, typename: str, build_slug: str | None) -> bool:
  """
  Report whether a counter delta may span a gap of ``code`` on ``typename``.

  Args:
    code (int): Negative sentinel code.
    typename (str): Monitor type name.
    build_slug (str | None): ``$build`` capability slug, or None when the
      stream carried no ``$build`` line.

  Returns:
    bool: True when the catalog marks the code bridgeable and the type's
    bridge is proven on this build.

  Examples:
    >>> is_bridgeable(-3, "host_ib", None)
    True
    >>> is_bridgeable(-2, "host_ib", None)
    False
  """
  return lookup(code).bridge and bridge_proven(typename, build_slug)


def sentinel_codes(values: np.ndarray) -> np.ndarray:
  """
  Per-entry sentinel code (negative) or 0 where the value is not a sentinel.

  Monitor values are unsigned, so a negative value is a sentinel code. This
  is the one validity gate for analysis (``build_job_frame``); SQL readers
  use ``value >= 0``. DCGM blank families stay a separate, GPU-scoped filter
  (``hpcperfstats.lib.dcgm_blank``): a large counter on another type can
  legitimately exceed the blank base.

  Args:
    values (np.ndarray): Float values (NaN for NULL).

  Returns:
    np.ndarray: int16 codes; 0 for data and NULL.

  Examples:
    >>> sentinel_codes(np.array([3.0, -2.0, np.nan])).tolist()
    [0, -2, 0]
  """
  v = np.asarray(values, dtype=np.float64)
  neg = np.isfinite(v) & (v < 0.0)
  out = np.zeros(v.shape, dtype=np.int16)
  out[neg] = np.maximum(v[neg], np.iinfo(np.int16).min).astype(np.int16)
  return out
