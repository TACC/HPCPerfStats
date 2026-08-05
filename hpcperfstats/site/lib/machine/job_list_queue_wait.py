"""
Queue wait (start_time − submit_time) contract for job list SQL aggregates and
  histograms.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from django.db.models import Avg, F, QuerySet
from django.db.models.functions import Extract


def filter_queue_wait_eligible(queryset: QuerySet) -> QuerySet:
    """
    Restrict to rows with a non-negative scheduling delay (start_time >=.
    
      submit_time).
    
    Args:
      queryset (QuerySet): Queryset.
    
    Returns:
      QuerySet: QuerySet produced by this call.
    
    Examples:
      >>> filter_queue_wait_eligible(None)  # doctest: +SKIP
    """
    return queryset.filter(start_time__gte=F("submit_time"))


def queue_wait_seconds_expression() -> Any:
    """
    Database expression: wait duration in seconds (epoch difference).
    
    Returns:
      Any: Open return polymorphism from ``queue_wait_seconds_expression``:
      concrete type depends on inputs and branch (mapping, scalar, handle, or
      ``None``-like empty).
    
    Examples:
      >>> queue_wait_seconds_expression()  # doctest: +SKIP
    """
    return Extract(F("start_time"), "epoch") - Extract(F("submit_time"), "epoch")


def aggregate_queue_wait_seconds_stats(queryset: QuerySet) -> dict:
    """
    Mean queue wait in seconds over eligible rows.
    
    Returns dict with key ``mean_wait_s`` (may be None when no eligible rows).
    
    Args:
      queryset (QuerySet): Queryset.
    
    Returns:
      dict: dict produced by this call.
    
    Examples:
      >>> aggregate_queue_wait_seconds_stats(None)  # doctest: +SKIP
    """
    qs = filter_queue_wait_eligible(queryset)
    expr = queue_wait_seconds_expression()
    return qs.aggregate(mean_wait_s=Avg(expr))


def queue_wait_hours_series(
  start_time: pd.Series,
  submit_time: pd.Series,
) -> pd.Series:
    """
    Per-row queue wait in hours; NaN where timestamps are missing or start <.
    
      submit.
    
    Aligns with :func:`filter_queue_wait_eligible` +
      ``queue_wait_seconds_expression`` /
    3600 so histograms and SQL aggregates describe the same quantity.
    
    Args:
      start_time (pd.Series): pandas Series.
      submit_time (pd.Series): pandas Series.
    
    Returns:
      pd.Series: pd.Series produced by this call.
    
    Examples:
      >>> queue_wait_hours_series(None, None)  # doctest: +SKIP
    """
    delta = start_time - submit_time
    sec = pd.to_timedelta(delta).dt.total_seconds()
    hours = sec / 3600.0
    invalid = start_time.isna() | submit_time.isna() | (start_time < submit_time)
    return hours.mask(invalid)
