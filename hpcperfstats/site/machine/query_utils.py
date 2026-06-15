"""Shared query param helpers: normalize date params to YYYY-MM-DD / YYYY-MM and expand month-only filters."""
import calendar
import re

# Job list sort: all columns except name (jobname). Maps URL order_by value to allowed Django field.
ORDER_BY_ALLOWED = frozenset({
    "jid",
    "username",
    "account",
    "start_time",
    "end_time",
    "runtime",
    "queue",
    "state",
    "ncores",
    "nhosts",
    "node_hrs",
    "performance_sort_rank",
    "metrics_distinct_time_count",
})

# Direct ``job_data.objects.filter(**kwargs)`` keys from SPA routes and extended search.
# ``host`` is handled separately as ``host_list__contains`` (see ``partition_job_list_acct_filters``).
_JOB_LIST_ACCT_FILTER_KEYS = frozenset({
    "jid",
    "username",
    "account",
    "account__icontains",
    "state",
    "queue",
    "end_time__date",
    "end_time__date__gte",
    "end_time__date__lte",
    "end_time__gte",
    "end_time__lte",
    "runtime__gte",
    "runtime__lte",
    "nhosts__gte",
    "nhosts__lte",
    "node_hrs__gte",
    "node_hrs__lte",
})


def get_job_list_order_by(fields):
    """Return order_by string for job_data queryset from fields dict, or None for default (-end_time).
    Accepts order_by value like 'end_time' (asc) or '-start_time' (desc). Only allowed fields are used.
    """
    raw = (fields or {}).get("order_by") or ""
    raw = (raw or "").strip()
    if not raw:
        return None
    desc = raw.startswith("-")
    field = raw.lstrip("-")
    if field == "sample_count":
        field = "metrics_distinct_time_count"
    if field not in ORDER_BY_ALLOWED:
        return None
    return f"-{field}" if desc else field


# Header toolbar multi-select filters (comma-separated query values, OR within dimension).
JOB_LIST_HEADER_MULTI_VALUE_FIELDS = frozenset({"username", "account", "queue", "state"})


def parse_job_list_multi_value_field(raw):
    """Split comma-separated filter values; strip, drop empties, dedupe preserving order."""
    if raw is None:
        return []
    if not isinstance(raw, str):
        raw = str(raw)
    seen = set()
    out = []
    for part in raw.split(","):
        token = part.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def parse_job_list_performance_sort_ranks(raw):
    """Parse comma-separated performance_sort_rank tokens (0–5); drop invalid."""
    ranks = []
    seen = set()
    for token in parse_job_list_multi_value_field(raw):
        try:
            rank = int(token)
        except (TypeError, ValueError):
            continue
        if rank < 0 or rank > 5 or rank in seen:
            continue
        seen.add(rank)
        ranks.append(rank)
    return ranks


def job_list_multi_value_orm_kwargs(field_name, values):
    """Map one or more string values to exact or ``__in`` ORM kwargs."""
    if not values:
        return {}
    if len(values) == 1:
        return {field_name: values[0]}
    return {f"{field_name}__in": values}


def apply_job_list_header_acct_multi_filters(acct_data):
    """Pop header multi-value keys from *acct_data*; return (remaining, ORM kwargs)."""
    data = dict(acct_data)
    kwargs = {}
    for field in JOB_LIST_HEADER_MULTI_VALUE_FIELDS:
        raw = data.pop(field, None)
        if raw in (None, ""):
            continue
        values = parse_job_list_multi_value_field(raw)
        kwargs.update(job_list_multi_value_orm_kwargs(field, values))
    return data, kwargs


def partition_job_list_acct_filters(acct_data):
    """Return (allowed_kwarg_dict, host_value) for job list ORM filters.

    Drops unknown keys so stray query parameters cannot raise FieldError during
    ``filter(**kwargs)``. Pulls ``host`` out for ``host_list__contains`` (SPA
    ``/machine/host/:host/``), since ``job_data`` has no ``host`` column.
    """
    data = dict(acct_data)
    raw_host = data.pop("host", None)
    host_val = str(raw_host).strip() if raw_host else ""
    host_val = host_val or None
    allowed = {k: v for k, v in data.items() if k in _JOB_LIST_ACCT_FILTER_KEYS}
    return allowed, host_val

# Shorthand date patterns (e.g. "2026-1" or "2026-1-5") that Django DateField rejects; normalize to YYYY-MM-DD.
_DATE_SHORTHAND = re.compile(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?(?:T.*)?$")
# Month-only format YYYY-MM (e.g. "2026-01") for whole-month filter.
_MONTH_ONLY = re.compile(r"^(\d{4})-(\d{2})$")
# Year-only format YYYY (e.g. "2024") for whole-year filter.
_YEAR_ONLY = re.compile(r"^(\d{4})$")
_DATE_NORMALIZE_KEYS = frozenset({
    "end_time__date",
    "end_time__date__gte",
    "end_time__date__lte",
    "end_time__gte",
    "end_time__lte",
    "start_time__date",
    "start_time__date__gte",
    "start_time__date__lte",
    "start_time__gte",
    "start_time__lte",
})


def normalize_date_param(value):
    """If value looks like YYYY-M or YYYY-M-D, return YYYY-MM-DD; otherwise return value unchanged."""
    if not value or not isinstance(value, str):
        return value
    value = value.strip()
    m = _DATE_SHORTHAND.match(value)
    if not m:
        return value
    y, month, day = m.group(1), int(m.group(2)), m.group(3)
    if day is None:
        return f"{y}-{month:02d}-01"
    return f"{y}-{month:02d}-{int(day):02d}"


def normalize_job_list_query_params(fields):
    """Return a copy of fields with date/datetime filter values normalized to YYYY-MM-DD where needed.
    Preserves end_time__date as YYYY-MM when given (so expand_month_date_to_range can expand to full month).
    """
    out = {}
    for k, v in fields.items():
        if k in _DATE_NORMALIZE_KEYS:
            # Keep month-only (YYYY-MM) or year-only (YYYY) end_time__date so expand_month_date_to_range can expand it
            if k == "end_time__date" and v and (_MONTH_ONLY.match(str(v).strip()) or _YEAR_ONLY.match(str(v).strip())):
                pass  # leave v unchanged
            else:
                v = normalize_date_param(v)
        out[k] = v
    return out


def expand_month_date_to_range(fields):
    """
    If fields contains end_time__date with a YYYY-MM value, replace it with
    end_time__date__gte and end_time__date__lte for that month. If YYYY only, expand to full year.
    Return dict suitable for filter.
    """
    out = dict(fields)
    val = out.get("end_time__date")
    if not val or not isinstance(val, str):
        return out
    val = val.strip()
    year_m = _YEAR_ONLY.match(val)
    if year_m:
        y = int(year_m.group(1))
        del out["end_time__date"]
        out["end_time__date__gte"] = f"{y}-01-01"
        out["end_time__date__lte"] = f"{y}-12-31"
        return out
    m = _MONTH_ONLY.match(val)
    if not m:
        return out
    y, month = int(m.group(1)), int(m.group(2))
    last_day = calendar.monthrange(y, month)[1]
    del out["end_time__date"]
    out["end_time__date__gte"] = f"{y}-{month:02d}-01"
    out["end_time__date__lte"] = f"{y}-{month:02d}-{last_day:02d}"
    return out
