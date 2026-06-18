"""Human-readable job list titles and filter summaries for extended search."""
from .job_list_performance import performance_status_label
from .job_list_state_groups import major_state_label, parse_major_state_filter_keys
from .query_utils import (
    parse_job_list_multi_value_field,
    parse_job_list_performance_sort_ranks,
)


def _metric_lines_from_fields(fields):
    lines = []
    for key, value in sorted(fields.items()):
        if not key.startswith("metrics_") or value in (None, ""):
            continue
        if key.endswith("__gte"):
            metric = key[len("metrics_") : -len("__gte")]
            lines.append(f"{metric} ≥ {value}")
        elif key.endswith("__lte"):
            metric = key[len("metrics_") : -len("__lte")]
            lines.append(f"{metric} ≤ {value}")
    return lines


def _multi_value_summary_line(label, raw):
    values = parse_job_list_multi_value_field(raw)
    if not values:
        return None
    if len(values) == 1:
        return f"{label}: {values[0]}"
    return f"{label}: {', '.join(values)}"


def build_job_list_qname_and_filter_summary(fields):
    """
    Return (qname, filter_summary) for job_list API responses.

    filter_summary is a list of short human-readable filter lines (may be empty).
    """
    lines = []
    queue_line = _multi_value_summary_line("Queue", fields.get("queue"))
    if queue_line:
        lines.append(queue_line)
    username_line = _multi_value_summary_line("User", fields.get("username"))
    if username_line:
        lines.append(username_line)
    host = (fields.get("host") or "").strip()
    if host:
        lines.append(f"Host: {host}")
    account_raw = fields.get("account__icontains") or fields.get("account")
    account_line = _multi_value_summary_line("Project", account_raw)
    if account_line:
        if fields.get("account__icontains"):
            lines.append(account_line.replace("Project:", "Project contains:", 1))
        else:
            lines.append(account_line)
    state_keys = parse_major_state_filter_keys(fields.get("state"))
    if state_keys:
        labels = [major_state_label(key) for key in state_keys]
        if len(labels) == 1:
            lines.append(f"Status: {labels[0]}")
        else:
            lines.append(f"Status: {', '.join(labels)}")
    perf_ranks = parse_job_list_performance_sort_ranks(fields.get("performance_sort_rank"))
    if perf_ranks:
        labels = [performance_status_label(rank) for rank in perf_ranks]
        if len(labels) == 1:
            lines.append(f"Performance: {labels[0]}")
        else:
            lines.append(f"Performance: {', '.join(labels)}")

    end_gte = fields.get("end_time__gte")
    end_lte = fields.get("end_time__lte")
    if end_gte:
        lines.append(f"Job ended on or after {end_gte}")
    if end_lte:
        lines.append(f"Job ended on or before {end_lte}")

    date_param = (fields.get("end_time__date") or "").strip()
    if date_param:
        if len(date_param) == 4 and date_param.isdigit():
            lines.append(f"Calendar year: {date_param}")
        else:
            lines.append(f"Job end date: {date_param}")

    for key, op_sym in (
        ("runtime__gte", "Runtime ≥"),
        ("runtime__lte", "Runtime ≤"),
        ("nhosts__gte", "Nodes ≥"),
        ("nhosts__lte", "Nodes ≤"),
        ("node_hrs__gte", "Node-hours ≥"),
        ("node_hrs__lte", "Node-hours ≤"),
    ):
        val = fields.get(key)
        if val not in (None, ""):
            lines.append(f"{op_sym} {val}")

    lines.extend(_metric_lines_from_fields(fields))

    date_param = (fields.get("end_time__date") or "").strip()
    queue_values = parse_job_list_multi_value_field(fields.get("queue"))
    if date_param and len(date_param) == 4 and date_param.isdigit():
        qname = f"Jobs for year {date_param}"
    elif date_param:
        qname = f"Jobs for date {date_param}"
    elif len(queue_values) == 1:
        qname = f"Jobs in queue {queue_values[0]}"
    elif host:
        qname = f"Jobs on host {host}"
    elif lines:
        qname = "Filtered jobs"
    else:
        qname = "Jobs"

    return qname, lines
