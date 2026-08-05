"""
Job list performance column: classify metrics coverage for display and sort
order.

sort_rank semantics (designation identity for filters / API
``performance.sort_rank``): 0 — Summary available (at least one metrics_data row
with non-null value). 1 — Not summarized yet (no metrics_data rows; runtime null
or >= SHORT threshold). 2 — Monitoring gaps (rows exist, all values null,
distinct_time_count >= 5). 3 — Job too short or too few samples (rows, all null,
0 < distinct_time_count < 5). 4 — Not summarized yet (UI label; rows, all null,
distinct_time_count null or <= 0). 5 — Too short to measure (no metrics_data
rows; runtime < SHORT threshold).

``performance_sort_group`` collapses designation ranks 1 and 4 into one primary
sort bucket (group 1). Public ``order_by=performance_sort_rank`` orders by that
group.

Attributes:
  MONITORING_GAPS_MIN_DISTINCT_TIMES: Attribute.
  PERFORMANCE_STATUS_BY_SORT_RANK: Attribute.
  SHORT_RUNTIME_NO_METRICS_SECONDS: Attribute.
"""
from __future__ import annotations

from typing import Any

from django.db.models import Case, Count, Exists, F, IntegerField, OuterRef, Q, Value, When

from .models import metrics_data

# Threshold aligned with product copy: many sample times but no usable metric values.
MONITORING_GAPS_MIN_DISTINCT_TIMES = 5
# Jobs shorter than this (seconds) with no metrics rows get a specific label (same sort_rank).
SHORT_RUNTIME_NO_METRICS_SECONDS = 600.0

# Canonical performance status labels keyed by sort_rank (header filter + filter_options).
# Ranks 1 and 4 share the same UI label; designation values stay distinct for filtering.
PERFORMANCE_STATUS_BY_SORT_RANK = (
    (0, "Summary available"),
    (1, "Not summarized yet"),
    (2, "Monitoring gaps"),
    (3, "Job too short or too few samples"),
    (4, "Not summarized yet"),
    (5, "Too short to measure"),
)


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


def summarize_performance(
  *,
  has_metrics_row: bool,
  metrics_value_count: int,
  distinct_time_count: int | None,
  runtime: float | None,
) -> dict:
    """
    Return display dict for the job list performance column.
    
    Keys: label, tone, aria_label, sort_rank (int).
    
    Args:
      has_metrics_row (bool): Whether to enable has metrics row.
      metrics_value_count (int): Integer value for metrics value count.
      distinct_time_count (int | None): One of ``int``, ``None``.
      runtime (float | None): One of ``float``, ``None``.
    
    Returns:
      dict: dict produced by this call.
    
    Examples:
      >>> summarize_performance(True, 0, None, None)  # doctest: +SKIP
    """

    def aria_label_for(text: str) -> str:
        """
        Aria label for.
        
        Args:
          text (str): String for text.
        
        Returns:
          str: str produced by this call.
        
        Examples:
          >>> aria_label_for("x")  # doctest: +SKIP
        """
        return f"Performance: {text}"

    if metrics_value_count > 0:
        label = "Summary available"
        return {
            "label": label,
            "tone": "success",
            "aria_label": aria_label_for(label),
            "sort_rank": 0,
        }
    if has_metrics_row:
        dtc = distinct_time_count
        if dtc is not None and dtc >= MONITORING_GAPS_MIN_DISTINCT_TIMES:
            label = "Monitoring gaps"
            return {
                "label": label,
                "tone": "info",
                "aria_label": aria_label_for(label),
                "sort_rank": 2,
            }
        if dtc is not None and 0 < dtc < MONITORING_GAPS_MIN_DISTINCT_TIMES:
            label = "Job too short or too few samples"
            return {
                "label": label,
                "tone": "warning",
                "aria_label": aria_label_for(label),
                "sort_rank": 3,
            }
        # Designation rank 4: same UI label as rank 1; distinct filter/API identity.
        label = "Not summarized yet"
        return {
            "label": label,
            "tone": "warning",
            "aria_label": aria_label_for(label),
            "sort_rank": 4,
        }
    if runtime is not None and runtime < SHORT_RUNTIME_NO_METRICS_SECONDS:
        label = "Too short to measure"
        return {
            "label": label,
            "tone": "secondary",
            "aria_label": aria_label_for(label),
            "sort_rank": 5,
        }
    label = "Not summarized yet"
    return {
        "label": label,
        "tone": "secondary",
        "aria_label": aria_label_for(label),
        "sort_rank": 1,
    }


def annotate_job_list_performance_fields(queryset: Any) -> Any:
    """
    Add has_metrics_data, metrics_value_count, performance_sort_rank,.
    
      performance_sort_group.
    
    Args:
      queryset (Any): Queryset passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> annotate_job_list_performance_fields(None)  # doctest: +SKIP
    """
    md_exists = Exists(metrics_data.objects.filter(jid_id=OuterRef("jid")))
    mcount = Count(
        "metrics_data_set",
        filter=Q(metrics_data_set__value__isnull=False),
    )
    qs = queryset.annotate(
        has_metrics_data=md_exists,
        metrics_value_count=mcount,
    )
    qs = qs.annotate(
        performance_sort_rank=Case(
            When(metrics_value_count__gt=0, then=Value(0)),
            When(
                Q(has_metrics_data=False)
                & (
                    Q(runtime__isnull=True)
                    | Q(runtime__gte=SHORT_RUNTIME_NO_METRICS_SECONDS)
                ),
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
            default=Value(5),
            output_field=IntegerField(),
        ),
    )
    # Ranks 1 and 4 share one primary sort bucket; other designation ranks keep their value.
    return qs.annotate(
        performance_sort_group=Case(
            When(performance_sort_rank=4, then=Value(1)),
            default=F("performance_sort_rank"),
            output_field=IntegerField(),
        ),
    )
