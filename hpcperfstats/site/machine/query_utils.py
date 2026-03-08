"""Shared query param helpers: normalize date params to YYYY-MM-DD / YYYY-MM and expand month-only filters."""
import calendar
import re

# Shorthand date patterns (e.g. "2026-1" or "2026-1-5") that Django DateField rejects; normalize to YYYY-MM-DD.
_DATE_SHORTHAND = re.compile(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?(?:T.*)?$")
# Month-only format YYYY-MM (e.g. "2026-01") for whole-month filter.
_MONTH_ONLY = re.compile(r"^(\d{4})-(\d{2})$")


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
    """Return a copy of fields with date/datetime filter values normalized to YYYY-MM-DD where needed."""
    out = {}
    for k, v in fields.items():
        if "time" in k and ("__date" in k or "__gte" in k or "__lte" in k):
            v = normalize_date_param(v)
        out[k] = v
    return out


def expand_month_date_to_range(fields):
    """
    If fields contains end_time__date with a YYYY-MM value, replace it with
    end_time__date__gte and end_time__date__lte for that month. Return dict suitable for filter.
    """
    out = dict(fields)
    val = out.get("end_time__date")
    if not val or not isinstance(val, str):
        return out
    m = _MONTH_ONLY.match(val.strip())
    if not m:
        return out
    y, month = int(m.group(1)), int(m.group(2))
    last_day = calendar.monthrange(y, month)[1]
    del out["end_time__date"]
    out["end_time__date__gte"] = f"{y}-{month:02d}-01"
    out["end_time__date__lte"] = f"{y}-{month:02d}-{last_day:02d}"
    return out
