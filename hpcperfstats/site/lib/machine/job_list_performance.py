"""Job list performance column: classify metrics coverage for display and sort order.

sort_rank semantics (ascending ``order_by("performance_sort_rank")`` matches product sort):
  0 — Summary available (at least one metrics_data row with non-null value).
  1 — Not summarized yet (no metrics_data rows; runtime null or >= SHORT threshold).
  2 — Monitoring gaps (rows exist, all values null, distinct_time_count >= 5).
  3 — Job too short or too few samples (rows, all null, 0 < distinct_time_count < 5).
  4 — Not enough samples to summarize (rows, all null, distinct_time_count null or <= 0).
  5 — Too short to measure (no metrics_data rows; runtime < SHORT threshold).
"""
from __future__ import annotations

from django.db.models import Case, Count, Exists, IntegerField, OuterRef, Q, Value, When

from .models import metrics_data

# Threshold aligned with product copy: many sample times but no usable metric values.
MONITORING_GAPS_MIN_DISTINCT_TIMES = 5
# Jobs shorter than this (seconds) with no metrics rows get a specific label (same sort_rank).
SHORT_RUNTIME_NO_METRICS_SECONDS = 120.0

# Canonical performance status labels keyed by sort_rank (header filter + filter_options).
PERFORMANCE_STATUS_BY_SORT_RANK = (
    (0, "Summary available"),
    (1, "Not summarized yet"),
    (2, "Monitoring gaps"),
    (3, "Job too short or too few samples"),
    (4, "Not enough samples to summarize"),
    (5, "Too short to measure"),
)


def performance_status_label(sort_rank):
    """Return display label for a performance_sort_rank integer."""
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
    """Return display dict for the job list performance column.

    Keys: label, tone, aria_label, sort_rank (int).
    """

    def aria_label_for(text: str) -> str:
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
        label = "Not enough samples to summarize"
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


def annotate_job_list_performance_fields(queryset):
    """Add has_metrics_data, metrics_value_count, performance_sort_rank for job_data querysets."""
    md_exists = Exists(metrics_data.objects.filter(jid_id=OuterRef("jid")))
    mcount = Count(
        "metrics_data_set",
        filter=Q(metrics_data_set__value__isnull=False),
    )
    qs = queryset.annotate(
        has_metrics_data=md_exists,
        metrics_value_count=mcount,
    )
    return qs.annotate(
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
