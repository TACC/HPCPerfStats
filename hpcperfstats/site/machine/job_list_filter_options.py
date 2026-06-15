"""Distinct header-filter option values scoped to the current job-list selection."""
from __future__ import annotations

from .job_list_performance import PERFORMANCE_STATUS_BY_SORT_RANK, performance_status_label
from .job_list_state_groups import major_state_options_from_raw

JOB_LIST_FILTER_OPTIONS_MAX = 200

_HEADER_STRING_DIMENSIONS = (
    ("usernames", "username"),
    ("accounts", "account"),
    ("queues", "queue"),
)


def _distinct_string_values(queryset, orm_field, cap=JOB_LIST_FILTER_OPTIONS_MAX):
    """Return sorted distinct non-empty string values, capped with truncation flag."""
    qs = (
        queryset.exclude(**{f"{orm_field}__isnull": True})
        .exclude(**{orm_field: ""})
        .values_list(orm_field, flat=True)
        .distinct()
        .order_by(orm_field)
    )
    values = list(qs[: cap + 1])
    truncated = len(values) > cap
    if truncated:
        values = values[:cap]
    return values, truncated


def _distinct_major_state_keys(queryset):
    """Return major terminal state group keys present in *queryset* (max five)."""
    raw_states = (
        queryset.exclude(state__isnull=True)
        .exclude(state="")
        .values_list("state", flat=True)
        .distinct()
    )
    return major_state_options_from_raw(raw_states)


def build_job_list_filter_options(request, build_queryset_from_request):
    """
    Faceted filter options for the job list header toolbar.

    For each dimension, options come from the queryset with all active filters
    except that dimension (so chips can be toggled off/on without empty lists).
    """
    truncated = {
        "usernames": False,
        "accounts": False,
        "queues": False,
        "states": False,
    }
    options = {
        "usernames": [],
        "accounts": [],
        "queues": [],
        "states": [],
        "performance_statuses": [],
        "truncated": truncated,
    }

    for response_key, orm_field in _HEADER_STRING_DIMENSIONS:
        qs, _fields, _cur, _order = build_queryset_from_request(
            request,
            exclude_header_dimension=orm_field,
        )
        values, is_truncated = _distinct_string_values(qs, orm_field)
        options[response_key] = values
        truncated[response_key] = is_truncated

    state_qs, _fields, _cur, _order = build_queryset_from_request(
        request,
        exclude_header_dimension="state",
    )
    options["states"] = _distinct_major_state_keys(state_qs)

    perf_qs, _fields, _cur, _order = build_queryset_from_request(
        request,
        exclude_header_dimension="performance_sort_rank",
    )
    rank_rows = (
        perf_qs.values_list("performance_sort_rank", flat=True)
        .distinct()
        .order_by("performance_sort_rank")
    )
    ranks_present = {rank for rank in rank_rows if rank is not None}
    options["performance_statuses"] = [
        {"sort_rank": rank, "label": performance_status_label(rank)}
        for rank, _label in PERFORMANCE_STATUS_BY_SORT_RANK
        if rank in ranks_present
    ]
    return options
