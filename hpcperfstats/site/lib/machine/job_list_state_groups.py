"""
Major terminal job-state groups for job list header filters.

Attributes:
  MAJOR_JOB_STATE_GROUPS: Attribute.
  _FAILED_BASE_TOKENS: Attribute.
  _VALID_MAJOR_STATE_KEYS: Attribute.
"""
from __future__ import annotations

from typing import Any

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


def _state_base_token(raw: Any) -> Any:
    """
    Internal helper to handle state base token.
    
    Args:
      raw (Any): Raw passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _state_base_token(None)  # doctest: +SKIP
    """
    if raw in (None, ""):
        return ""
    return str(raw).strip().split("+", 1)[0].strip().upper()


def classify_job_state(raw: Any) -> Any:
    """
    Map a raw Slurm state string to a major group key, or None if not.
    
      filterable.
    
    Args:
      raw (Any): Raw passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> classify_job_state(None)  # doctest: +SKIP
    """
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


def major_state_label(key: Any) -> Any:
    """
    Major state label.
    
    Args:
      key (Any): Key passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> major_state_label(None)  # doctest: +SKIP
    """
    for group_key, label in MAJOR_JOB_STATE_GROUPS:
        if group_key == key:
            return label
    return key


def parse_major_state_filter_keys(raw: Any) -> Any:
    """
    Parse comma-separated major state group keys from the query string.
    
    Args:
      raw (Any): Raw passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> parse_major_state_filter_keys(None)  # doctest: +SKIP
    """
    keys = []
    seen = set()
    for token in parse_job_list_multi_value_field(raw):
        key = token.lower()
        if key not in _VALID_MAJOR_STATE_KEYS or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def _major_state_q_for_key(key: Any) -> Any:
    """
    Internal helper to handle major state q for key.
    
    Args:
      key (Any): Key passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _major_state_q_for_key(None)  # doctest: +SKIP
    """
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


def major_state_q(group_keys: Any) -> Any:
    """
    OR together Q filters for selected major state groups.
    
    Args:
      group_keys (Any): Group keys passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> major_state_q(None)  # doctest: +SKIP
    """
    if not group_keys:
        return Q()
    combined = _major_state_q_for_key(group_keys[0])
    for key in group_keys[1:]:
        combined |= _major_state_q_for_key(key)
    return combined


def major_state_options_from_raw(raw_states: Any) -> Any:
    """
    Collapse raw distinct DB states into sorted major group keys.
    
    Args:
      raw_states (Any): Raw states passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> major_state_options_from_raw(None)  # doctest: +SKIP
    """
    present = set()
    for raw in raw_states:
        key = classify_job_state(raw)
        if key:
            present.add(key)
    return [key for key, _label in MAJOR_JOB_STATE_GROUPS if key in present]
