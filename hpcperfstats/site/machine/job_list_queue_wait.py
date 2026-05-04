"""Queue wait (start_time − submit_time) contract for job list SQL aggregates and histograms."""

from __future__ import annotations

import pandas as pd
from django.db.models import Avg, F, QuerySet, StdDev
from django.db.models.functions import Extract


def filter_queue_wait_eligible(queryset: QuerySet) -> QuerySet:
    """Restrict to rows with a non-negative scheduling delay (start_time >= submit_time)."""
    return queryset.filter(start_time__gte=F("submit_time"))


def queue_wait_seconds_expression():
    """Database expression: wait duration in seconds (epoch difference)."""
    return Extract(F("start_time"), "epoch") - Extract(F("submit_time"), "epoch")


def aggregate_queue_wait_seconds_stats(queryset: QuerySet) -> dict:
    """
    Mean and sample standard deviation of queue wait in seconds over eligible rows.

    Returns dict with keys ``mean_wait_s`` and ``std_wait_s`` (each may be None).
    """
    qs = filter_queue_wait_eligible(queryset)
    expr = queue_wait_seconds_expression()
    return qs.aggregate(mean_wait_s=Avg(expr), std_wait_s=StdDev(expr))


def queue_wait_hours_series(start_time: pd.Series, submit_time: pd.Series) -> pd.Series:
    """
    Per-row queue wait in hours; NaN where timestamps are missing or start < submit.

    Aligns with :func:`filter_queue_wait_eligible` + ``queue_wait_seconds_expression`` /
    3600 so histograms and SQL aggregates describe the same quantity.
    """
    delta = start_time - submit_time
    sec = pd.to_timedelta(delta).dt.total_seconds()
    hours = sec / 3600.0
    invalid = start_time.isna() | submit_time.isna() | (start_time < submit_time)
    return hours.mask(invalid)
