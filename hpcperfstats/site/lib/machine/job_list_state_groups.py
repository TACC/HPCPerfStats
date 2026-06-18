"""Major terminal job-state groups for job list header filters."""
from __future__ import annotations

from django.db.models import Q

from .query_utils import parse_job_list_multi_value_field

MAJOR_JOB_STATE_GROUPS = (
    ("completed", "Completed"),
    ("failed", "Failed"),
    ("canceled", "Canceled"),
    ("preempted", "Preempted"),
    ("timeout", "Timeout"),
)

_FAILED_BASE_TOKENS = frozenset({"FAILED", "OUT_OF_MEMORY", "NODE_FAIL"})
_VALID_MAJOR_STATE_KEYS = frozenset(key for key, _label in MAJOR_JOB_STATE_GROUPS)


def _state_base_token(raw):
    if raw in (None, ""):
        return ""
    return str(raw).strip().split("+", 1)[0].strip().upper()


def classify_job_state(raw):
    """Map a raw Slurm state string to a major group key, or None if not filterable."""
    token = _state_base_token(raw)
    if not token:
        return None
    if token == "COMPLETED":
        return "completed"
    if token == "TIMEOUT":
        return "timeout"
    if token in _FAILED_BASE_TOKENS:
        return "failed"
    if token.startswith("CANCEL"):
        return "canceled"
    if token.startswith("PREEMPT"):
        return "preempted"
    return None


def major_state_label(key):
    for group_key, label in MAJOR_JOB_STATE_GROUPS:
        if group_key == key:
            return label
    return key


def parse_major_state_filter_keys(raw):
    """Parse comma-separated major state group keys from the query string."""
    keys = []
    seen = set()
    for token in parse_job_list_multi_value_field(raw):
        key = token.lower()
        if key not in _VALID_MAJOR_STATE_KEYS or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def _major_state_q_for_key(key):
    if key == "completed":
        return Q(state__iexact="COMPLETED") | Q(state__istartswith="COMPLETED+")
    if key == "failed":
        q = Q()
        for tok in _FAILED_BASE_TOKENS:
            q |= Q(state__iexact=tok) | Q(state__istartswith=f"{tok}+")
        return q
    if key == "canceled":
        return Q(state__istartswith="CANCEL")
    if key == "preempted":
        return Q(state__istartswith="PREEMPT")
    if key == "timeout":
        return Q(state__iexact="TIMEOUT") | Q(state__istartswith="TIMEOUT+")
    return Q(pk__in=[])


def major_state_q(group_keys):
    """OR together Q filters for selected major state groups."""
    if not group_keys:
        return Q()
    combined = _major_state_q_for_key(group_keys[0])
    for key in group_keys[1:]:
        combined |= _major_state_q_for_key(key)
    return combined


def major_state_options_from_raw(raw_states):
    """Collapse raw distinct DB states into sorted major group keys."""
    present = set()
    for raw in raw_states:
        key = classify_job_state(raw)
        if key:
            present.add(key)
    return [key for key, _label in MAJOR_JOB_STATE_GROUPS if key in present]
