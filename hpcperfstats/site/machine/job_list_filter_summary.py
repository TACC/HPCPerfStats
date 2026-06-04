"""Human-readable job list titles and filter summaries for extended search."""


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


def build_job_list_qname_and_filter_summary(fields):
    """
    Return (qname, filter_summary) for job_list API responses.

    filter_summary is a list of short human-readable filter lines (may be empty).
    """
    lines = []
    queue = (fields.get("queue") or "").strip()
    username = (fields.get("username") or "").strip()
    host = (fields.get("host") or "").strip()
    account = (fields.get("account__icontains") or fields.get("account") or "").strip()
    state = (fields.get("state") or "").strip()

    if queue:
        lines.append(f"Queue: {queue}")
    if username:
        lines.append(f"User: {username}")
    if host:
        lines.append(f"Host: {host}")
    if account:
        lines.append(f"Project contains: {account}")
    if state:
        lines.append(f"State: {state}")

    end_gte = fields.get("end_time__gte")
    end_lte = fields.get("end_time__lte")
    if end_gte:
        lines.append(f"Job ended on or after {end_gte}")
    if end_lte:
        lines.append(f"Job ended on or before {end_lte}")

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
    if date_param and len(date_param) == 4 and date_param.isdigit():
        qname = f"Jobs for year {date_param}"
    elif date_param:
        qname = f"Jobs for date {date_param}"
    elif queue:
        qname = f"Jobs in queue {queue}"
    elif host:
        qname = f"Jobs on host {host}"
    elif lines:
        qname = "Filtered jobs"
    else:
        qname = "Jobs"

    return qname, lines
