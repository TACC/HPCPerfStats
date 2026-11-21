"""
Transient vs static monitor value sentinels in the 5-minute metric fold.

``build_job_frame`` keeps each sentinel code in ``JobFrame.sentinel`` and
sets ``value`` NaN there, so a reader that ignores the codes sees a sentinel
as NULL. This module is the reader that does not: for one ``(type, events)``
fold it turns the scoped rows into per-sample states, classifies every host
series, and folds the job value with the sentinel rules:

* **Samples.** A ``(host, time)`` sample is *valid*, *bridged* (every
  sentinel row in it is a bridgeable code whose counter delta the next valid
  row spans, so the measured rate is spread over it) or a *gap* (any other
  sentinel row). One sentinel event therefore never leaves a partial
  multi-event sum: the whole sample is a gap (intersection mask).
* **Partial devices.** Collapsed per-device rows store the sum of the
  readable devices; ``host_sentinel_summary`` runs give the unreadable count
  ``U`` of ``N``. Up to ``partial_device_max_frac`` the row is scaled by
  ``N / (N - U)``; above it the sample is a gap.
* **Classes.** :func:`classify_series` labels a host clean, transient (value
  computed from the covered samples, interior gap buckets interpolated by
  :func:`fill_interior_gaps`) or static (excluded from the metric).
* **Hosts.** Mean folds use the remaining hosts; a host-summed fold with a
  static host is None unless ``scale_partial_sums``; no host left, or job
  coverage under the floor, is None.

The row reductions (per-sample states, the per-host bucket fold and the
interior fill) run in the baked Codon kernels when ``codon_metrics
status=enabled``; the ``_np_*`` functions here are their NumPy twins (host
``.venv`` and parity tests, ``codon-python-parity-contract.mdc``).

Per-sample sums outside the fold (plots, Type Detail, peaks, the
``avg_cpuusage`` busy/idle sums) use the same sample states through
:func:`sentinel_sample_mask`: a gap sample is missing, never a partial sum.
Series readers interpolate its interior gaps with :func:`fill_sample_gaps`
under the fold's fill limits; peaks are never filled, and nothing outside the
fold is scaled by partial-device runs. Jobs without sentinel rows and without
partial-device runs never reach this module (the legacy paths run
unchanged).

Attributes:
  CLEAN (str): Series without sentinel samples.
  TRANSIENT (str): Series with sentinel samples, covered at or above the
    floor.
  STATIC (str): Series unavailable on this host.
  SAMPLE_VALID (int): Sample state of a measured sample.
  SAMPLE_BRIDGED (int): Sample state of a bridged sentinel sample.
  SAMPLE_GAP (int): Sample state of an unrecovered sentinel sample.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from hpcperfstats.analysis.metrics.lib.columnar import codon_bridge
from hpcperfstats.analysis.metrics.lib.columnar.segments import (
  FIVE_MIN_US,
  TIME_BITS,
)
from hpcperfstats.analysis.metrics.lib.sentinel_reasons import SentinelRun
from hpcperfstats.lib.monitor_sentinel import lookup

CLEAN = "clean"
TRANSIENT = "transient"
STATIC = "static"
SAMPLE_VALID = 0
SAMPLE_BRIDGED = 1
SAMPLE_GAP = 2


@dataclass(frozen=True)
class SentinelConfig:
  """
  ``[PIPELINE] metrics_sentinel_*`` tunables for one job's folds.

  Attributes:
    min_coverage (float): Coverage floor of a transient series.
    scale_partial_sums (bool): Scale a host-summed fold missing static
      hosts instead of returning None.
    partial_device_max_frac (float): Largest unreadable-device share that
      is scaled; above it the sample is a gap.
    fill_max_gap_s (float): Longest interpolated gap (gap-bucket seconds).
    fill_max_frac (float): Largest interpolated share of a host's buckets.

  Examples:
    >>> SentinelConfig().min_coverage
    0.5
  """

  min_coverage: float = 0.5
  scale_partial_sums: bool = False
  partial_device_max_frac: float = 0.30
  fill_max_gap_s: float = 900.0
  fill_max_frac: float = 0.10

  @classmethod
  def from_ini(cls) -> SentinelConfig:
    """
    Read the five ``[PIPELINE] metrics_sentinel_*`` keys.

    Returns:
      SentinelConfig: Configured tunables.

    Examples:
      >>> 0.0 <= SentinelConfig.from_ini().min_coverage <= 1.0
      True
    """
    import hpcperfstats.dbload.lib.conf_parser as cfg

    return cls(
      min_coverage=cfg.get_metrics_sentinel_min_coverage(),
      scale_partial_sums=cfg.get_metrics_sentinel_scale_partial_sums(),
      partial_device_max_frac=cfg.get_metrics_sentinel_partial_device_max_frac(),
      fill_max_gap_s=cfg.get_metrics_sentinel_fill_max_gap_s(),
      fill_max_frac=cfg.get_metrics_sentinel_fill_max_frac(),
    )


@dataclass
class SentinelProfile:
  """
  Sample counts of one host series in one fold, measured before any fill.

  Attributes:
    valid (int): Measured samples.
    bridged (int): Sentinel samples whose activity a bridge recovered.
    sentinel (int): Unrecovered sentinel (gap) samples.
    codes (dict[int, int]): ``{code: samples}`` over bridged and gap samples.

  Examples:
    >>> SentinelProfile(3, 0, 1, {-3: 1}).valid_frac
    0.75
  """

  valid: int = 0
  bridged: int = 0
  sentinel: int = 0
  codes: dict[int, int] = field(default_factory=dict)

  @property
  def valid_frac(self) -> float:
    """
    ``(valid + bridged) / (valid + bridged + sentinel)``; 1.0 without
    samples. Counting samples, not time, keeps slow-tier keys unpenalized.

    Returns:
      float: Covered share.

    Examples:
      >>> SentinelProfile().valid_frac
      1.0
    """
    total = self.valid + self.bridged + self.sentinel
    return 1.0 if total == 0 else (self.valid + self.bridged) / total


def classify_series(profile: SentinelProfile, min_coverage: float) -> str:
  """
  Label one host series ``clean``, ``transient`` or ``static``.

  Static: coverage under ``min_coverage``, or a static-kind code (counter
  not present, not built, permission denied) with no covered sample.
  Transient: any sentinel sample otherwise. Clean: none.

  Args:
    profile (SentinelProfile): Series counts.
    min_coverage (float): Coverage floor.

  Returns:
    str: :data:`CLEAN`, :data:`TRANSIENT` or :data:`STATIC`.

  Examples:
    >>> classify_series(SentinelProfile(1, 0, 1, {-3: 1}), 0.5)
    'transient'
    >>> classify_series(SentinelProfile(0, 0, 2, {-2: 2}), 0.0)
    'static'
  """
  if profile.sentinel == 0 and profile.bridged == 0:
    return CLEAN
  covered = profile.valid + profile.bridged
  if profile.valid_frac < min_coverage or (
    covered == 0 and any(lookup(c).kind == STATIC for c in profile.codes)
  ):
    return STATIC
  return TRANSIENT


def _np_fill_interior_gaps(
  bucket_means: np.ndarray,
  gap_mask: np.ndarray,
  max_gap_buckets: int,
  max_frac: float,
  bucket_ix: np.ndarray | None = None,
) -> tuple[np.ndarray, int]:
  """
  NumPy twin of the Codon ``fill_interior_gaps_c`` kernel.

  Same contract as :func:`fill_interior_gaps`.

  Args:
    bucket_means (np.ndarray): Bucket means in time order (NaN in gaps).
    gap_mask (np.ndarray): True where a bucket is a sentinel gap.
    max_gap_buckets (int): Longest fillable run of gap buckets.
    max_frac (float): Largest fillable share of the buckets.
    bucket_ix (np.ndarray | None): Bucket positions for the distance
      weights; default consecutive.

  Returns:
    tuple[np.ndarray, int]: ``(filled copy, interpolated bucket count)``.

  Examples:
    >>> out, n = _np_fill_interior_gaps(
    ...   np.array([1.0, np.nan, 3.0]), np.array([False, True, False]), 3, 0.5
    ... )
    >>> out.tolist(), n
    ([1.0, 2.0, 3.0], 1)
  """
  means = np.asarray(bucket_means, dtype=np.float64).copy()
  gaps = np.asarray(gap_mask, dtype=bool)
  pos = (
    np.arange(means.shape[0], dtype=np.float64)
    if bucket_ix is None
    else np.asarray(bucket_ix, dtype=np.float64)
  )
  anchor = ~gaps & np.isfinite(means)
  fill: list[tuple[int, int]] = []
  i, n = 0, means.shape[0]
  while i < n:
    if not gaps[i]:
      i += 1
      continue
    j = i
    while j < n and gaps[j]:
      j += 1
    if (
      i > 0
      and j < n
      and anchor[i - 1]
      and anchor[j]
      and j - i <= max_gap_buckets
    ):
      fill.append((i, j))
    i = j
  n_fill = sum(j - i for i, j in fill)
  if n_fill == 0 or n_fill > max_frac * n:
    return means, 0
  for i, j in fill:
    x0, x1 = pos[i - 1], pos[j]
    y0, y1 = means[i - 1], means[j]
    means[i:j] = y0 + (y1 - y0) * (pos[i:j] - x0) / (x1 - x0)
  return means, n_fill


def fill_interior_gaps(
  bucket_means: np.ndarray,
  gap_mask: np.ndarray,
  max_gap_buckets: int,
  max_frac: float,
  bucket_ix: np.ndarray | None = None,
) -> tuple[np.ndarray, int]:
  """
  Linearly interpolate interior gap buckets of one host series.

  A run of consecutive gap buckets is filled only when it has a finite
  non-gap bucket on both sides (never an edge) and at most
  ``max_gap_buckets`` buckets. When the buckets to fill exceed ``max_frac``
  of the series, nothing is filled. Non-gap buckets (including buckets that
  are NaN for other reasons) are never touched. Runs the Codon kernel when
  baked, else :func:`_np_fill_interior_gaps`.

  Args:
    bucket_means (np.ndarray): Bucket means in time order (NaN in gaps).
    gap_mask (np.ndarray): True where a bucket is a sentinel gap.
    max_gap_buckets (int): Longest fillable run of gap buckets.
    max_frac (float): Largest fillable share of the buckets.
    bucket_ix (np.ndarray | None): Bucket positions for the distance
      weights; default consecutive.

  Returns:
    tuple[np.ndarray, int]: ``(filled copy, interpolated bucket count)``.

  Examples:
    >>> out, n = fill_interior_gaps(
    ...   np.array([1.0, np.nan, 3.0]), np.array([False, True, False]), 3, 0.5
    ... )
    >>> out.tolist(), n
    ([1.0, 2.0, 3.0], 1)
  """
  if codon_bridge.kernels() is None:
    return _np_fill_interior_gaps(
      bucket_means, gap_mask, max_gap_buckets, max_frac, bucket_ix
    )
  n = int(np.shape(bucket_means)[0])
  pos = np.arange(n, dtype=np.float64) if bucket_ix is None else bucket_ix
  return codon_bridge.fill_interior_gaps(
    bucket_means, gap_mask, pos, max_gap_buckets, max_frac
  )


@dataclass
class FoldQuality:
  """
  What the sentinel rules did to one fold, for the reason and staff note.

  Attributes:
    typename (str): Concrete type of the fold.
    n_hosts (int): Hosts with samples in the fold.
    excluded (dict[str, int]): Static host -> its most frequent code.
    transient_codes (dict[int, int]): ``{code: samples}`` over the hosts
      kept in the fold.
    covered (int): Valid plus bridged samples of the kept hosts.
    samples (int): All samples of the kept hosts.
    bridged (int): Bridged samples.
    filled (int): Interpolated buckets.
    buckets (int): Buckets of the kept hosts after the first-bucket drop.
    partial_devices (dict[int, list[int]]): ``code -> [max unreadable
      devices, total devices]`` over scaled samples.
    hosts_scaled (bool): A host-summed value was scaled for static hosts.
    none_reason (str | None): ``all_static``, ``sum_static`` or
      ``under_floor`` when the fold returned None.

  Examples:
    >>> FoldQuality("host_ib", 2).clean
    True
  """

  typename: str
  n_hosts: int
  excluded: dict[str, int] = field(default_factory=dict)
  transient_codes: dict[int, int] = field(default_factory=dict)
  covered: int = 0
  samples: int = 0
  bridged: int = 0
  filled: int = 0
  buckets: int = 0
  partial_devices: dict[int, list[int]] = field(default_factory=dict)
  hosts_scaled: bool = False
  none_reason: str | None = None

  @property
  def clean(self) -> bool:
    """
    True when no sentinel rule changed the fold.

    Returns:
      bool: No exclusion, sentinel sample, bridge, fill, scaling or None
      decision.

    Examples:
      >>> FoldQuality("t", 1, bridged=1).clean
      False
    """
    return not (
      self.excluded
      or self.transient_codes
      or self.partial_devices
      or self.none_reason
      or self.bridged
      or self.filled
      or self.hosts_scaled
    )


def _most_common(codes: Mapping[int, int]) -> int:
  """
  Most frequent code (ties: smallest magnitude).

  Args:
    codes (Mapping[int, int]): ``{code: count}``.

  Returns:
    int: Code.

  Examples:
    >>> _most_common({-2: 3, -3: 3, -5: 1})
    -2
  """
  return max(codes.items(), key=lambda kv: (kv[1], kv[0]))[0]


def sentinel_fold(
  host: np.ndarray,
  time_us: np.ndarray,
  values: np.ndarray,
  status: np.ndarray,
  codes: np.ndarray,
  conv: float,
  *,
  typename: str,
  host_names: Sequence[str] | None = None,
  host_aggregate: str = "mean",
  config: SentinelConfig | None = None,
) -> tuple[float | None, FoldQuality]:
  """
  Fold per-sample states to the ``job_arc`` scalar under the sentinel rules.

  Same bucketing as ``segments.bucket_host_means`` (5-minute bucket means of
  covered samples, first-bucket drop, per-host mean, then mean or sum of
  hosts, ``conv`` on bucket means), plus host classification, interior gap
  fill and the static-host rules described in the module docstring.

  Args:
    host (np.ndarray): Host id per sample.
    time_us (np.ndarray): Sample time per sample.
    values (np.ndarray): Sample value (ignored for gap samples).
    status (np.ndarray): :data:`SAMPLE_VALID`, :data:`SAMPLE_BRIDGED` or
      :data:`SAMPLE_GAP` per sample.
    codes (np.ndarray): Sentinel code per sample (0 for valid).
    conv (float): Multiplier on bucket means.
    typename (str): Type named in the quality record.
    host_names (Sequence[str] | None): Host name by id for the excluded
      hosts (ids as text when None).
    host_aggregate (str): ``mean`` or ``sum`` across hosts.
    config (SentinelConfig | None): Tunables (defaults when None).

  Returns:
    tuple[float | None, FoldQuality]: Value (None per the static rules,
    NaN when every kept host mean is NaN) and the quality record.

  Examples:
    >>> v, q = sentinel_fold(
    ...   np.array([0, 0]),
    ...   np.array([0, 60_000_000]),
    ...   np.array([2.0, np.nan]),
    ...   np.array([SAMPLE_VALID, SAMPLE_GAP]),
    ...   np.array([0, -3]),
    ...   1.0,
    ...   typename="t",
    ... )
    >>> v, q.transient_codes
    (2.0, {-3: 1})
  """
  cfg = config or SentinelConfig()
  host = np.asarray(host, dtype=np.int64)
  values = np.asarray(values, dtype=np.float64)
  status = np.asarray(status, dtype=np.int64)
  codes = np.asarray(codes, dtype=np.int64)
  bucket = np.asarray(time_us, dtype=np.int64) // FIVE_MIN_US
  max_gap_buckets = int(cfg.fill_max_gap_s // (FIVE_MIN_US / 1_000_000))
  fold = (
    _np_sentinel_fold
    if codon_bridge.kernels() is None
    else codon_bridge.sentinel_fold
  )
  per_host, means, _gaps, _pos = fold(
    host, bucket, values, status, max_gap_buckets, cfg.fill_max_frac
  )
  q = FoldQuality(typename, int(per_host.shape[1]))
  all_covered = int(np.count_nonzero(status != SAMPLE_GAP))
  sentinel_rows = np.flatnonzero(codes != 0)
  s_host, s_code = host[sentinel_rows], codes[sentinel_rows]
  host_means: list[float] = []
  for h, n_valid, n_bridged, n_gap, b0, nb, n_filled in per_host.T.tolist():
    prof = SentinelProfile(valid=n_valid, bridged=n_bridged, sentinel=n_gap)
    for c in s_code[s_host == h].tolist():
      prof.codes[c] = prof.codes.get(c, 0) + 1
    if classify_series(prof, cfg.min_coverage) == STATIC:
      name = host_names[h] if host_names is not None else str(h)
      q.excluded[name] = _most_common(prof.codes)
      continue
    for c, n in prof.codes.items():
      q.transient_codes[c] = q.transient_codes.get(c, 0) + n
    q.covered += prof.valid + prof.bridged
    q.samples += prof.valid + prof.bridged + prof.sentinel
    q.bridged += prof.bridged
    q.filled += n_filled
    q.buckets += nb
    host_bucket_means = means[b0 : b0 + nb]
    fin = host_bucket_means[np.isfinite(host_bucket_means)]
    host_means.append(float(fin.mean()) * float(conv) if fin.size else np.nan)
  if not host_means:
    q.none_reason = "all_static"
    return None, q
  if all_covered < cfg.min_coverage * status.shape[0]:
    q.none_reason = "under_floor"
    return None, q
  fin = np.array([m for m in host_means if np.isfinite(m)], dtype=np.float64)
  if fin.size == 0:
    return float("nan"), q
  if host_aggregate != "sum":
    return float(fin.mean()), q
  if q.excluded:
    if not cfg.scale_partial_sums:
      q.none_reason = "sum_static"
      return None, q
    q.hosts_scaled = True
    return float(fin.sum()) * q.n_hosts / len(host_means), q
  return float(fin.sum()), q


def _np_sentinel_fold(
  host: np.ndarray,
  bucket: np.ndarray,
  values: np.ndarray,
  status: np.ndarray,
  max_gap_buckets: int,
  max_frac: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
  """
  NumPy twin of the Codon ``sentinel_fold_c`` kernel.

  Per host (ascending): 5-minute bucket mean of the covered samples (row
  order ``np.bincount`` sums; NaN and a gap flag when no sample is covered),
  first-bucket drop when the host has more than one bucket, then
  :func:`_np_fill_interior_gaps` at the bucket positions.

  Args:
    host (np.ndarray): int64 host id per sample.
    bucket (np.ndarray): int64 5-minute bucket index per sample.
    values (np.ndarray): float64 sample value (ignored for gap samples).
    status (np.ndarray): int64 :data:`SAMPLE_VALID` / :data:`SAMPLE_BRIDGED`
      / :data:`SAMPLE_GAP` per sample.
    max_gap_buckets (int): Longest fillable run of gap buckets.
    max_frac (float): Largest fillable share of a host's buckets.

  Returns:
    tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]: ``(per_host,
    means, gaps, positions)``: int64 ``(7, hosts)`` rows host id, valid /
    bridged / gap sample counts, bucket offset, bucket count and filled
    count, then the filled bucket means, gap flags and float bucket
    positions packed by host.

  Examples:
    >>> per_host, means, gaps, pos = _np_sentinel_fold(
    ...   np.array([0, 0, 0]),
    ...   np.array([0, 1, 1]),
    ...   np.array([5.0, 2.0, 4.0]),
    ...   np.array([SAMPLE_VALID, SAMPLE_VALID, SAMPLE_GAP]),
    ...   3,
    ...   0.1,
    ... )
    >>> per_host[:, 0].tolist(), means.tolist(), gaps.tolist(), pos.tolist()
    ([0, 2, 0, 1, 0, 1, 0], [2.0], [False], [1.0])
  """
  hosts = np.unique(host)
  per_host = np.zeros(
    (codon_bridge.SENTINEL_FOLD_HOST_ROWS, hosts.shape[0]), dtype=np.int64
  )
  means_out = [np.empty(0, dtype=np.float64)]
  gaps_out = [np.empty(0, dtype=bool)]
  pos_out = [np.empty(0, dtype=np.float64)]
  offset = 0
  for k, h in enumerate(hosts.tolist()):
    sel = host == h
    st = status[sel]
    ub, inv = np.unique(bucket[sel], return_inverse=True)
    covered = st != SAMPLE_GAP
    cnt = np.bincount(
      inv, weights=covered.astype(np.float64), minlength=ub.size
    )
    tot = np.bincount(
      inv, weights=np.where(covered, values[sel], 0.0), minlength=ub.size
    )
    with np.errstate(invalid="ignore", divide="ignore"):
      means = np.where(cnt > 0, tot / cnt, np.nan)
    gap = cnt == 0
    if ub.size > 1:
      ub, means, gap = ub[1:], means[1:], gap[1:]
    means, n_filled = _np_fill_interior_gaps(
      means, gap, max_gap_buckets, max_frac, ub
    )
    per_host[:, k] = (
      h,
      np.count_nonzero(st == SAMPLE_VALID),
      np.count_nonzero(st == SAMPLE_BRIDGED),
      np.count_nonzero(st == SAMPLE_GAP),
      offset,
      ub.size,
      n_filled,
    )
    means_out.append(means)
    gaps_out.append(gap)
    pos_out.append(ub.astype(np.float64))
    offset += int(ub.size)
  return (
    per_host,
    np.concatenate(means_out),
    np.concatenate(gaps_out),
    np.concatenate(pos_out),
  )


def _run_unreadable(
  runs: Sequence[SentinelRun],
  t_us: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, list[dict[int, int]]]:
  """
  Unreadable devices per time from partial-device runs of one series.

  Overlapping runs of the same code (archive and live ingest racing) are
  unioned: each code counts its largest device count covering ``t``, and
  different codes add up.

  Args:
    runs (Sequence[SentinelRun]): Partial runs of one host/type/dev/event.
    t_us (np.ndarray): Row times.

  Returns:
    tuple[np.ndarray, np.ndarray, list[dict[int, int]]]: Unreadable count,
    total devices, and ``{code: devices}`` per row.

  Examples:
    >>> r = SentinelRun("h", "t", "", "e", -3, 0, 10, 2, 8)
    >>> u, n, per = _run_unreadable([r], np.array([5, 20]))
    >>> u.tolist(), n.tolist(), per
    ([2, 0], [8, 0], [{-3: 2}, {}])
  """
  unread = np.zeros(t_us.shape[0], dtype=np.int64)
  total = np.zeros(t_us.shape[0], dtype=np.int64)
  per: list[dict[int, int]] = [{} for _ in range(t_us.shape[0])]
  for r in runs:
    hit = np.flatnonzero((t_us >= r.start_us) & (t_us <= r.end_us))
    for i in hit.tolist():
      per[i][r.code] = max(per[i].get(r.code, 0), r.device_count)
      total[i] = max(total[i], r.total_devices)
  for i, d in enumerate(per):
    unread[i] = sum(d.values())
  return unread, total, per


def sentinel_samples(
  index: Any,
  typ: str,
  events: Sequence[str] | None,
  col: str,
  runs: Iterable[SentinelRun],
  *,
  limit: float | None,
  nonnegative: bool = False,
  per_sample_sum: bool = True,
  config: SentinelConfig | None = None,
  force: bool = False,
) -> tuple[tuple[np.ndarray, ...], dict[int, list[int]]] | None:
  """
  Per-sample states of one fold, or None without sentinel evidence.

  Reads the scoped rows of ``typ`` / ``events`` from a ``FrameIndex``. A row
  is valid when non-NULL, under ``limit`` and (``nonnegative``) not
  negative; a sentinel row is bridged when its code is bridgeable and the
  next row of its chain is a valid row with a finite ``arc`` (ingest spanned
  the gap), whose rate it takes. Partial-device runs scale or gap the
  collapsed rows they cover.

  Args:
    index (Any): ``FrameIndex`` of the job.
    typ (str): Concrete type name.
    events (Sequence[str] | None): Probe-expanded events.
    col (str): ``arc`` (counter rates; bridges apply) or ``value``.
    runs (Iterable[SentinelRun]): Job ``host_sentinel_summary`` runs.
    limit (float | None): Absolute ceiling of a valid row, or None.
    nonnegative (bool): Negative rows are not valid.
    per_sample_sum (bool): Sum rows per ``(host, time)`` (``arc`` folds);
      False keeps one sample per row (gauge means).
    config (SentinelConfig | None): Tunables (defaults when None).
    force (bool): Build the samples even without sentinel evidence (the
      other side of an aligned ratio has some).

  Returns:
    tuple[tuple[np.ndarray, ...], dict[int, list[int]]] | None:
    ``((host, time_us, values, status, codes), partial_devices)``, or None
    when the rows carry no sentinel and no partial-device run applies.

  Examples:
    >>> from hpcperfstats.analysis.metrics.lib.columnar.jobframe import (
    ...   JobFrame,
    ... )
    >>> from hpcperfstats.analysis.metrics.lib.columnar.kernels import (
    ...   FrameIndex,
    ... )
    >>> f = JobFrame.from_records(
    ...   "1", [(1, "h", "cpu", "user", None, 1, 1.0, 1.0, None)]
    ... )
    >>> sentinel_samples(FrameIndex(f), "cpu", ["user"], "arc", [], limit=None)
  """
  cfg = config or SentinelConfig()
  f = index.frame
  rows = index.scoped_rows(
    typ, index.rows_for_series(index._select(typ, events))
  )
  if rows.size == 0:
    return None
  code = f.sentinel[rows].astype(np.int64)
  host_names = f.hosts
  known = set(host_names)
  partial = [
    r for r in runs if r.type == typ and not r.full and r.host in known
  ]
  ev_of = [f.series[s][1] for s in range(len(f.series))]
  if partial:
    keep_ev = {ev_of[s] for s in np.unique(f.series_id[rows]).tolist()}
    partial = [r for r in partial if r.event in keep_ev]
  if not code.any() and not partial and not force:
    return None
  vals = index.values_at("arc" if col == "arc" else "value", rows)
  valid = ~np.isnan(vals)
  if limit is not None:
    valid &= np.abs(vals) < float(limit)
  if nonnegative:
    valid &= vals >= 0.0
  valid &= code == 0
  eff = np.where(valid, vals, 0.0)
  bridged = (
    _bridge_rows(f, rows, code, vals, valid, eff)
    if col == "arc" and code.any()
    else np.zeros(rows.shape[0], dtype=bool)
  )
  partial_devices: dict[int, list[int]] = {}
  if partial:
    t = f.time_us[rows].astype(np.int64)
    for h_name in {r.host for r in partial}:
      hid = host_names.index(h_name)
      for ev in {r.event for r in partial if r.host == h_name}:
        sel = np.flatnonzero(
          (f.host_id[rows] == hid)
          & np.isin(
            f.series_id[rows], [s for s, e in enumerate(ev_of) if e == ev]
          )
          & (code == 0)
        )
        hr = [r for r in partial if r.host == h_name and r.event == ev]
        unread, total, per = _run_unreadable(hr, t[sel])
        for k, i in enumerate(sel.tolist()):
          if unread[k] == 0 or total[k] <= unread[k]:
            continue
          if unread[k] > cfg.partial_device_max_frac * total[k]:
            valid[i] = False
            code[i] = _most_common(per[k])
            continue
          eff[i] *= total[k] / (total[k] - unread[k])
          for c, n in per[k].items():
            pd = partial_devices.setdefault(c, [0, int(total[k])])
            pd[0] = max(pd[0], n)
            pd[1] = max(pd[1], int(total[k]))
  gap_row = (code != 0) & ~bridged
  host = f.host_id[rows].astype(np.int64)
  tus = f.time_us[rows].astype(np.int64)
  if not per_sample_sum:
    keep = valid | (code != 0)
    status = np.where(gap_row, SAMPLE_GAP, SAMPLE_VALID)
    return (
      host[keep],
      tus[keep],
      eff[keep],
      status[keep],
      np.where(gap_row, code, 0)[keep],
    ), partial_devices
  key = (host << 32) | index._time_index[rows].astype(np.int64)
  reduce = (
    _np_sentinel_samples
    if codon_bridge.kernels() is None
    else codon_bridge.sentinel_samples
  )
  first, sums, status, scodes = reduce(key, valid, bridged, gap_row, code, eff)
  return (host[first], tus[first], sums, status, scodes), partial_devices


def _bridge_rows(
  f: Any,
  rows: np.ndarray,
  code: np.ndarray,
  vals: np.ndarray,
  valid: np.ndarray,
  eff: np.ndarray,
) -> np.ndarray:
  """
  Bridge sentinel rows to the next valid row of their counter chain.

  ``rows`` are frame rows sorted by ``(series, host, dev, time)``. A
  bridgeable code whose next chain row is valid takes that row's rate in
  ``eff``. Otherwise the next row, when it is not a sentinel, lost its
  interval with the gap and becomes a gap row too: ``code`` takes the
  breaking code and ``valid`` turns False there (both updated in place).

  Args:
    f (Any): ``JobFrame`` the rows index.
    rows (np.ndarray): Frame row positions.
    code (np.ndarray): int64 sentinel code per row (updated in place).
    vals (np.ndarray): ``arc`` per row.
    valid (np.ndarray): Row is a measured value (updated in place).
    eff (np.ndarray): Effective value per row (updated in place).

  Returns:
    np.ndarray: True where a sentinel row is bridged.

  Examples:
    >>> from hpcperfstats.analysis.metrics.lib.columnar.jobframe import (
    ...   JobFrame,
    ... )
    >>> f = JobFrame.from_records(
    ...   "1",
    ...   [
    ...     (1, "h", "t", "e", None, 1, -1.0, None, None),
    ...     (2, "h", "t", "e", None, 1, 5.0, 3.0, None),
    ...   ],
    ... )
    >>> eff = np.zeros(2)
    >>> (
    ...   _bridge_rows(
    ...     f,
    ...     np.arange(2),
    ...     np.array([-1, 0]),
    ...     f.arc,
    ...     np.array([False, True]),
    ...     eff,
    ...   ).tolist(),
    ...   eff.tolist(),
    ... )
    ([True, False], [3.0, 0.0])
  """
  n = rows.shape[0]
  bridged = np.zeros(n, dtype=bool)
  chain = (
    (f.series_id[rows].astype(np.int64) << 40)
    | (f.host_id[rows].astype(np.int64) << 20)
    | (f.dev_id[rows].astype(np.int64) & 0xFFFFF)
  )
  nxt = np.where(code == 0, np.arange(n), n)
  nxt = np.minimum.accumulate(nxt[::-1])[::-1]
  broken: dict[int, int] = {}
  for i in np.flatnonzero(code != 0).tolist():
    j = int(nxt[i])
    if j >= n or chain[j] != chain[i]:
      continue
    if valid[j] and lookup(int(code[i])).bridge:
      bridged[i] = True
      eff[i] = vals[j]
    elif code[j] == 0:
      # After a chain break the next row's interval is lost with the gap
      # (ingest leaves its rate NULL), so it is a gap sample too: no
      # partial sum there, and a code this consumer does not know never
      # bridges even if a rate is present.
      broken[j] = int(code[i])
  for j, c in broken.items():
    code[j] = c
    valid[j] = False
  return bridged


def sentinel_sample_mask(
  index: Any,
  rows: np.ndarray,
  sample_key: np.ndarray,
  vals: np.ndarray,
  valid: np.ndarray,
  col: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
  """
  Row view of the fold's sample states for a per-sample sum.

  Rows of a static-kind code (counter not present, not built, permission
  denied) are absent: the counter does not exist there, so the sum of the
  other events is complete. Every other sentinel row is bridged (``arc``
  only: it takes the next valid rate of its chain) or makes its whole
  sample a gap, as does the valid row after a chain break, so one
  unreadable event never leaves a partial sum (intersection mask, the same
  states as :func:`sentinel_samples`).

  Args:
    index (Any): ``FrameIndex`` of the job.
    rows (np.ndarray): Scoped frame rows of the sum, in frame order.
    sample_key (np.ndarray): int64 sample key per row (``(host, time)``, or
      ``(host, dev, time)`` for per-device sums).
    vals (np.ndarray): Summed column per row.
    valid (np.ndarray): Row contributes to its sample (NULL, ceiling and
      sign filters already applied).
    col (str): ``arc`` (bridges apply), ``value`` or ``delta``.

  Returns:
    tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]: ``(keep, vals,
    valid, gap_keys)``: rows to keep (no static row, no row of a gap
    sample), values and validity with the bridged rates applied, and the
    sorted keys of the gap samples.

  Examples:
    >>> from hpcperfstats.analysis.metrics.lib.columnar.jobframe import (
    ...   JobFrame,
    ... )
    >>> from hpcperfstats.analysis.metrics.lib.columnar.kernels import (
    ...   FrameIndex,
    ... )
    >>> f = JobFrame.from_records(
    ...   "1",
    ...   [
    ...     (1, "h", "t", "a", None, 1, 1.0, 2.0, None),
    ...     (1, "h", "t", "b", None, 1, -5.0, None, None),
    ...   ],
    ... )
    >>> keep, _v, _ok, gaps = sentinel_sample_mask(
    ...   FrameIndex(f),
    ...   np.arange(2),
    ...   np.array([7, 7]),
    ...   f.arc,
    ...   np.array([True, False]),
    ...   "arc",
    ... )
    >>> keep.tolist(), gaps.tolist()
    ([False, False], [7])
  """
  code = index.frame.sentinel[rows].astype(np.int64)
  static_codes = [
    c for c in np.unique(code[code != 0]).tolist() if lookup(c).kind == STATIC
  ]
  live = np.flatnonzero(~np.isin(code, static_codes))
  key, code = sample_key[live], code[live]
  vals_l, ok = vals[live], valid[live].copy()
  eff = np.where(ok, vals_l, 0.0)
  bridged = (
    _bridge_rows(index.frame, rows[live], code, vals_l, ok, eff)
    if col == "arc" and code.any()
    else np.zeros(live.shape[0], dtype=bool)
  )
  reduce = (
    _np_sentinel_samples
    if codon_bridge.kernels() is None
    else codon_bridge.sentinel_samples
  )
  first, _sums, status, _codes = reduce(
    key, ok, bridged, (code != 0) & ~bridged, code, eff
  )
  gap_keys = key[first[status == SAMPLE_GAP]]
  keep = np.zeros(rows.shape[0], dtype=bool)
  keep[live[~np.isin(key, gap_keys)]] = True
  out_vals, out_valid = vals.copy(), valid.copy()
  out_vals[live] = np.where(bridged, eff, vals_l)
  out_valid[live] = ok | bridged
  return keep, out_vals, out_valid, gap_keys


def fill_sample_gaps(
  keys: np.ndarray,
  sums: np.ndarray,
  gap_keys: np.ndarray,
  time_axis: np.ndarray,
  config: SentinelConfig,
) -> tuple[np.ndarray, np.ndarray, int]:
  """
  Interpolate the interior gap samples of per-sample sums (series readers).

  ``keys`` are sorted ``segments.composite_key`` sample keys whose low
  ``TIME_BITS`` index ``time_axis`` and whose high bits name the series
  (host, or host and device). The gap samples are merged in as NaN and each
  series is filled by :func:`fill_interior_gaps` with the fold's limits:
  ``fill_max_gap_s`` converted to samples by the series' median spacing
  and ``fill_max_frac`` of its samples. Gap samples left unfilled (edges,
  long runs, too large a share) are dropped, so they stay missing.

  Args:
    keys (np.ndarray): Sorted int64 keys of the measured samples.
    sums (np.ndarray): Sum per key.
    gap_keys (np.ndarray): Keys of the gap samples (disjoint from
      ``keys``).
    time_axis (np.ndarray): int64 sample time (us) by time index.
    config (SentinelConfig): ``fill_max_gap_s`` / ``fill_max_frac``.

  Returns:
    tuple[np.ndarray, np.ndarray, int]: ``(keys, sums, filled)`` with the
    filled gap samples merged in, and their count.

  Examples:
    >>> k, s, n = fill_sample_gaps(
    ...   np.array([0, 2]),
    ...   np.array([1.0, 3.0]),
    ...   np.array([1]),
    ...   np.array([0, 60_000_000, 120_000_000]),
    ...   SentinelConfig(fill_max_frac=0.5),
    ... )
    >>> k.tolist(), s.tolist(), n
    ([0, 1, 2], [1.0, 2.0, 3.0], 1)
  """
  merged = np.concatenate([keys, gap_keys])
  order = np.argsort(merged, kind="stable")
  merged = merged[order]
  vals = np.concatenate([sums, np.full(gap_keys.shape[0], np.nan)])[order]
  gap = order >= keys.shape[0]
  t_us = time_axis[merged & ((1 << TIME_BITS) - 1)].astype(np.float64)
  series = merged >> TIME_BITS
  cut = (np.flatnonzero(np.diff(series)) + 1).tolist()
  filled = 0
  for lo, hi in zip([0, *cut], [*cut, merged.shape[0]], strict=True):
    if hi - lo < 3 or not gap[lo:hi].any():
      continue
    step = float(np.median(np.diff(t_us[lo:hi])))
    max_run = int(config.fill_max_gap_s * 1e6 // step) if step > 0 else 0
    if max_run < 1:
      continue
    vals[lo:hi], n = fill_interior_gaps(
      vals[lo:hi], gap[lo:hi], max_run, config.fill_max_frac, t_us[lo:hi]
    )
    filled += n
  keep = ~gap | ~np.isnan(vals)
  return merged[keep], vals[keep], filled


def _np_sentinel_samples(
  keys: np.ndarray,
  valid: np.ndarray,
  bridged: np.ndarray,
  gap_row: np.ndarray,
  code: np.ndarray,
  eff: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
  """
  NumPy twin of the Codon ``sentinel_samples_c`` kernel.

  One sample per distinct key (sorted): ``eff`` of its valid or bridged
  rows summed in row order (``np.bincount``), status gap over bridged over
  valid, code of its last gap row else its last bridged row else 0, and its
  first row. Keys whose rows are neither valid, bridged nor gap are dropped.

  Args:
    keys (np.ndarray): int64 ``(host, time)`` sample key per row.
    valid (np.ndarray): Row is a measured value.
    bridged (np.ndarray): Row is a bridged sentinel.
    gap_row (np.ndarray): Row is an unrecovered sentinel.
    code (np.ndarray): int64 sentinel code per row.
    eff (np.ndarray): float64 effective value per row.

  Returns:
    tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]: ``(first_row,
    sums, status, codes)`` per kept sample.

  Examples:
    >>> first, sums, status, codes = _np_sentinel_samples(
    ...   np.array([5, 5, 9]),
    ...   np.array([True, False, False]),
    ...   np.array([False, False, True]),
    ...   np.array([False, True, False]),
    ...   np.array([0, -3, -1]),
    ...   np.array([2.0, 0.0, 4.0]),
    ... )
    >>> first.tolist(), sums.tolist(), status.tolist(), codes.tolist()
    ([0, 2], [2.0, 4.0], [2, 1], [-3, -1])
  """
  uk, inv = np.unique(keys, return_inverse=True)
  n = uk.shape[0]
  any_gap = np.bincount(inv, weights=gap_row, minlength=n) > 0
  any_bridge = np.bincount(inv, weights=bridged, minlength=n) > 0
  any_valid = np.bincount(inv, weights=valid, minlength=n) > 0
  # bincount returns int64 for empty input even with weights.
  sums = np.bincount(
    inv, weights=np.where(valid | bridged, eff, 0.0), minlength=n
  ).astype(np.float64, copy=False)
  scode = np.zeros(n, dtype=np.int64)
  scode[inv[gap_row]] = code[gap_row]
  bcode = np.zeros(n, dtype=np.int64)
  bcode[inv[bridged]] = code[bridged]
  status = np.where(
    any_gap, SAMPLE_GAP, np.where(any_bridge, SAMPLE_BRIDGED, SAMPLE_VALID)
  )
  keep = any_gap | any_bridge | any_valid
  first = np.zeros(n, dtype=np.int64)
  first[inv[::-1]] = np.arange(keys.shape[0])[::-1]
  return (
    first[keep],
    sums[keep],
    status[keep],
    np.where(any_gap, scode, bcode)[keep],
  )


def align_gaps(
  samples: tuple[np.ndarray, ...], other: tuple[np.ndarray, ...]
) -> tuple[np.ndarray, ...]:
  """
  Mark the samples of one ratio side that are gaps on the other side.

  Derived ratios fold numerator and denominator separately; a sample that is
  a sentinel gap on either side becomes a gap on both, so both folds cover
  identical intervals.

  Args:
    samples (tuple[np.ndarray, ...]): ``(host, time_us, values, status,
      codes)`` of this side.
    other (tuple[np.ndarray, ...]): The same arrays of the other side.

  Returns:
    tuple[np.ndarray, ...]: ``samples`` with the other side's gaps applied.

  Examples:
    >>> a = (
    ...   np.array([0, 0]),
    ...   np.array([1, 2]),
    ...   np.array([1.0, 1.0]),
    ...   np.array([0, 0]),
    ...   np.array([0, 0]),
    ... )
    >>> b = (
    ...   np.array([0]),
    ...   np.array([2]),
    ...   np.array([0.0]),
    ...   np.array([2]),
    ...   np.array([-5]),
    ... )
    >>> align_gaps(a, b)[3].tolist()
    [0, 2]
  """
  host, t, vals, status, codes = samples
  o_host, o_t, _v, o_status, o_codes = other
  gap = o_status == SAMPLE_GAP
  key = (host.astype(np.int64) << 44) | (t.astype(np.int64) & ((1 << 44) - 1))
  o_key = (o_host[gap].astype(np.int64) << 44) | (
    o_t[gap].astype(np.int64) & ((1 << 44) - 1)
  )
  hit = np.isin(key, o_key) & (status != SAMPLE_GAP)
  if not hit.any():
    return samples
  status = status.copy()
  codes = codes.copy()
  status[hit] = SAMPLE_GAP
  idx = np.searchsorted(np.sort(o_key), key[hit])
  codes[hit] = o_codes[gap][np.argsort(o_key)][idx]
  return host, t, vals, status, codes
