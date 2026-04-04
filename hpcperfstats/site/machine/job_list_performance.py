"""Job list performance column: classify metrics coverage for display and sort order.

sort_rank semantics (ascending order_by = best first, then increasing severity):
  0 — At least one metrics_data row with non-null value.
  1 — Rows exist, all values null, metrics_distinct_time_count >= 5 (monitoring gaps).
  2 — Rows exist, all values null, 0 < metrics_distinct_time_count < 5.
  3 — Rows exist, all values null, metrics_distinct_time_count is NULL or <= 0.
  4 — No metrics_data rows (optionally labeled for very short runtime).
"""
from __future__ import annotations

from django.db.models import Case, Count, Exists, IntegerField, OuterRef, Q, Value, When

from .models import metrics_data

# Threshold aligned with product copy: many sample times but no usable metric values.
MONITORING_GAPS_MIN_DISTINCT_TIMES = 5
# Jobs shorter than this (seconds) with no metrics rows get a specific label (same sort_rank).
SHORT_RUNTIME_NO_METRICS_SECONDS = 120.0


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
                "sort_rank": 1,
            }
        if dtc is not None and 0 < dtc < MONITORING_GAPS_MIN_DISTINCT_TIMES:
            label = "Job too short or too few samples"
            return {
                "label": label,
                "tone": "warning",
                "aria_label": aria_label_for(label),
                "sort_rank": 2,
            }
        label = "Not enough samples to summarize"
        return {
            "label": label,
            "tone": "warning",
            "aria_label": aria_label_for(label),
            "sort_rank": 3,
        }
    if runtime is not None and runtime < SHORT_RUNTIME_NO_METRICS_SECONDS:
        label = "Too short to measure"
        return {
            "label": label,
            "tone": "secondary",
            "aria_label": aria_label_for(label),
            "sort_rank": 4,
        }
    label = "Not summarized yet"
    return {
        "label": label,
        "tone": "secondary",
        "aria_label": aria_label_for(label),
        "sort_rank": 4,
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
                Q(has_metrics_data=True)
                & Q(metrics_distinct_time_count__gte=MONITORING_GAPS_MIN_DISTINCT_TIMES),
                then=Value(1),
            ),
            When(
                Q(has_metrics_data=True)
                & Q(metrics_distinct_time_count__gt=0)
                & Q(metrics_distinct_time_count__lt=MONITORING_GAPS_MIN_DISTINCT_TIMES),
                then=Value(2),
            ),
            When(
                Q(has_metrics_data=True)
                & (
                    Q(metrics_distinct_time_count__isnull=True)
                    | Q(metrics_distinct_time_count__lte=0)
                ),
                then=Value(3),
            ),
            default=Value(4),
            output_field=IntegerField(),
        ),
    )
