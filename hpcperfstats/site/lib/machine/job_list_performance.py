"""
Job list performance column: classify metrics + artifact readiness for display
and sort order.

sort_rank semantics (designation identity for filters / API
``performance.sort_rank``):
0 — Metrics & Plots available (non-null metrics values and fresh plot/detail
artifacts). 1 — Metrics available (non-null values; artifacts not ready).
2–4 — Too few samples to complete (metrics rows exist, all values null; ranks
differ by ``metrics_distinct_time_count``). 5 — Too short to complete (no
metrics rows; runtime < SHORT threshold). 6 — Metrics & Plots not yet completed
(no metrics rows; runtime null, == SHORT, or > SHORT).

``performance_sort_group`` collapses ranks 2–4 into one primary sort bucket
(group 2). Public ``order_by=performance_sort_rank`` orders by that group.

Attributes:
  LABEL_METRICS_AND_PLOTS_AVAILABLE: Display label for sort_rank 0.
  LABEL_METRICS_AVAILABLE: Display label for sort_rank 1.
  LABEL_NOT_YET_COMPLETED: Display label for sort_rank 6.
  LABEL_TOO_FEW_SAMPLES: Display label for sort_ranks 2–4.
  LABEL_TOO_SHORT: Display label for sort_rank 5.
  MONITORING_GAPS_MIN_DISTINCT_TIMES: Attribute.
  PERFORMANCE_STATUS_BY_SORT_RANK: Attribute.
  SHORT_RUNTIME_NO_METRICS_SECONDS: Attribute.
  TOO_FEW_SAMPLES_SORT_RANKS: Attribute.
"""
from __future__ import annotations

from typing import Any

from django.db.models import Case, Exists, F, IntegerField, OuterRef, Q, Value, When

from hpcperfstats.dbload.lib import conf_parser as cfg

from .artifact_readiness_expressions import annotate_job_plots_artifacts_ready
from .models import metrics_data

# Threshold for rank 2 vs 3 when metrics rows exist but all values are null.
MONITORING_GAPS_MIN_DISTINCT_TIMES = 5
# Jobs shorter than this (seconds) with no metrics rows → Too short to complete.
SHORT_RUNTIME_NO_METRICS_SECONDS = 600.0

LABEL_METRICS_AND_PLOTS_AVAILABLE = "Metrics & Plots available"
LABEL_METRICS_AVAILABLE = "Metrics available"
LABEL_TOO_FEW_SAMPLES = "Too few samples to complete"
LABEL_TOO_SHORT = "Too short to complete"
LABEL_NOT_YET_COMPLETED = "Metrics & Plots not yet completed"

# Canonical performance status labels keyed by sort_rank (header filter + filter_options).
# Ranks 2–4 share the same UI label; designation values stay distinct for filtering.
PERFORMANCE_STATUS_BY_SORT_RANK = (
    (0, LABEL_METRICS_AND_PLOTS_AVAILABLE),
    (1, LABEL_METRICS_AVAILABLE),
    (2, LABEL_TOO_FEW_SAMPLES),
    (3, LABEL_TOO_FEW_SAMPLES),
    (4, LABEL_TOO_FEW_SAMPLES),
    (5, LABEL_TOO_SHORT),
    (6, LABEL_NOT_YET_COMPLETED),
)

TOO_FEW_SAMPLES_SORT_RANKS = (2, 3, 4)


def performance_status_label(sort_rank: Any) -> Any:
  """
  Return display label for a performance_sort_rank integer.

  Args:
    sort_rank (Any): Sort rank passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> performance_status_label(None)  # doctest: +SKIP
  """
  for rank, label in PERFORMANCE_STATUS_BY_SORT_RANK:
    if rank == sort_rank:
      return label
  return str(sort_rank)


def expand_performance_sort_ranks_for_filter(ranks: Any) -> Any:
  """
  Expand shared-label ranks so filtering one Too-few rank covers 2–4.

  Args:
    ranks (Any): Iterable of int ranks from the query string.

  Returns:
    Any: Deduplicated list of ranks (order preserved for first occurrence).

  Examples:
    >>> expand_performance_sort_ranks_for_filter([2])
    [2, 3, 4]
  """
  out = []
  seen = set()
  expand_few = False
  for rank in ranks or []:
    if rank in TOO_FEW_SAMPLES_SORT_RANKS:
      expand_few = True
      continue
    if rank not in seen:
      seen.add(rank)
      out.append(rank)
  if expand_few:
    for rank in TOO_FEW_SAMPLES_SORT_RANKS:
      if rank not in seen:
        seen.add(rank)
        out.append(rank)
  return out


def summarize_performance(
  *,
  has_metrics_row: bool,
  metrics_value_count: int,
  distinct_time_count: int | None,
  runtime: float | None,
  plots_artifacts_ready: bool = False,
) -> dict:
  """
  Return display dict for the job list performance column.

  Keys: label, tone, aria_label, sort_rank (int).

  Args:
    has_metrics_row (bool): Whether metrics_data rows exist.
    metrics_value_count (int): Count of non-null metric values.
    distinct_time_count (int | None): Persisted distinct sample-time count.
    runtime (float | None): Job runtime seconds.
    plots_artifacts_ready (bool): Fingerprint-matching plot/detail artifacts.

  Returns:
    dict: dict produced by this call.

  Examples:
    >>> summarize_performance(True, 0, None, None)  # doctest: +SKIP
  """

  def aria_label_for(text: str) -> str:
    """
    Build the screen-reader label for a performance status string.

    Args:
      text (str): Visible performance status label.

    Returns:
      str: Prefixed aria label.

    Examples:
      >>> aria_label_for("Metrics available")
      'Performance: Metrics available'
    """
    return f"Performance: {text}"

  if metrics_value_count > 0:
    if plots_artifacts_ready:
      label = LABEL_METRICS_AND_PLOTS_AVAILABLE
      return {
          "label": label,
          "tone": "success",
          "aria_label": aria_label_for(label),
          "sort_rank": 0,
      }
    label = LABEL_METRICS_AVAILABLE
    return {
        "label": label,
        "tone": "info",
        "aria_label": aria_label_for(label),
        "sort_rank": 1,
    }
  if has_metrics_row:
    dtc = distinct_time_count
    if dtc is not None and dtc >= MONITORING_GAPS_MIN_DISTINCT_TIMES:
      label = LABEL_TOO_FEW_SAMPLES
      return {
          "label": label,
          "tone": "warning",
          "aria_label": aria_label_for(label),
          "sort_rank": 2,
      }
    if dtc is not None and 0 < dtc < MONITORING_GAPS_MIN_DISTINCT_TIMES:
      label = LABEL_TOO_FEW_SAMPLES
      return {
          "label": label,
          "tone": "warning",
          "aria_label": aria_label_for(label),
          "sort_rank": 3,
      }
    label = LABEL_TOO_FEW_SAMPLES
    return {
        "label": label,
        "tone": "warning",
        "aria_label": aria_label_for(label),
        "sort_rank": 4,
    }
  if runtime is not None and runtime < SHORT_RUNTIME_NO_METRICS_SECONDS:
    label = LABEL_TOO_SHORT
    return {
        "label": label,
        "tone": "secondary",
        "aria_label": aria_label_for(label),
        "sort_rank": 5,
    }
  # No metrics rows; runtime null, == 600, or > 600 — not yet through update_metrics.
  label = LABEL_NOT_YET_COMPLETED
  return {
      "label": label,
      "tone": "secondary",
      "aria_label": aria_label_for(label),
      "sort_rank": 6,
  }


def _job_list_host_name_suffix() -> str:
  """
  FQDN suffix for plot fingerprint SQL (``.<host_name_ext>``).

  Returns:
    str: Dot-prefixed host name extension, or empty string when unset.

  Examples:
    >>> _job_list_host_name_suffix()  # doctest: +SKIP
  """
  ext = cfg.get_host_name_ext()
  if not ext:
    return ""
  return "." + str(ext).lstrip(".")


def annotate_job_list_performance_fields(queryset: Any) -> Any:
  """
  Add has_metrics_data, metrics_value_count, plots_artifacts_ready,
  performance_sort_rank, performance_sort_group.

  ``metrics_value_count`` is 1 or 0 from ``Exists`` (any non-null metric
  value). A reverse-relation ``Count`` forced ``GROUP BY`` plus a
  ``metrics_data`` join; ``QuerySet.count()`` on a day's listing then
  exceeded PostgreSQL ``statement_timeout`` (``job_list: count() failed``).

  Args:
    queryset (Any): Queryset passed to this helper.

  Returns:
    Any: Annotated queryset.

  Examples:
    >>> annotate_job_list_performance_fields(None)  # doctest: +SKIP
  """
  qs = annotate_job_plots_artifacts_ready(queryset, _job_list_host_name_suffix())
  md_exists = Exists(metrics_data.objects.filter(jid_id=OuterRef("jid")))
  has_nonnull_metric = Exists(
      metrics_data.objects.filter(
          jid_id=OuterRef("jid"),
          value__isnull=False,
      )
  )
  qs = qs.annotate(
      has_metrics_data=md_exists,
      metrics_value_count=Case(
          When(has_nonnull_metric, then=Value(1)),
          default=Value(0),
          output_field=IntegerField(),
      ),
  )
  qs = qs.annotate(
      performance_sort_rank=Case(
          When(
              Q(metrics_value_count__gt=0) & Q(plots_artifacts_ready=True),
              then=Value(0),
          ),
          When(
              Q(metrics_value_count__gt=0) & Q(plots_artifacts_ready=False),
              then=Value(1),
          ),
          When(
              Q(has_metrics_data=True)
              & Q(metrics_distinct_time_count__gte=MONITORING_GAPS_MIN_DISTINCT_TIMES),
              then=Value(2),
          ),
          When(
              Q(has_metrics_data=True)
              & Q(metrics_distinct_time_count__gt=0)
              & Q(metrics_distinct_time_count__lt=MONITORING_GAPS_MIN_DISTINCT_TIMES),
              then=Value(3),
          ),
          When(
              Q(has_metrics_data=True)
              & (
                  Q(metrics_distinct_time_count__isnull=True)
                  | Q(metrics_distinct_time_count__lte=0)
              ),
              then=Value(4),
          ),
          When(
              Q(has_metrics_data=False)
              & Q(runtime__isnull=False)
              & Q(runtime__lt=SHORT_RUNTIME_NO_METRICS_SECONDS),
              then=Value(5),
          ),
          default=Value(6),
          output_field=IntegerField(),
      ),
  )
  # Ranks 2–4 share one primary sort bucket (Too few samples to complete).
  return qs.annotate(
      performance_sort_group=Case(
          When(performance_sort_rank__in=list(TOO_FEW_SAMPLES_SORT_RANKS), then=Value(2)),
          default=F("performance_sort_rank"),
          output_field=IntegerField(),
      ),
  )
