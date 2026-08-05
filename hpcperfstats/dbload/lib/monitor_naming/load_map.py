"""
Load docs/monitor_variable_rename_map.yaml (single source of truth).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


def _repo_root() -> Path:
    """
    Internal helper to handle repo root.
    
    Returns:
      Path: Path produced by this call.
    
    Examples:
      >>> _repo_root()  # doctest: +SKIP
    """
    return Path(__file__).resolve().parents[2]


def yaml_path() -> Path:
    """
    Yaml path.
    
    Returns:
      Path: Path produced by this call.
    
    Examples:
      >>> yaml_path()  # doctest: +SKIP
    """
    packaged = Path(__file__).resolve().parent / "monitor_variable_rename_map.yaml"
    if packaged.is_file():
        return packaged
    return _repo_root() / "docs" / "monitor_variable_rename_map.yaml"


@lru_cache(maxsize=1)
def load_monitor_rename_map() -> dict[str, Any]:
    """
    Load the monitor rename map.
    
    Returns:
      dict[str, Any]: dict[str, Any] produced by this call.
    
    Raises:
      RuntimeError: Raised when ``load_monitor_rename_map`` hits a
      ``RuntimeError`` failure path.
      ValueError: Raised when ``load_monitor_rename_map`` hits a
      ``ValueError`` failure path.
    
    Examples:
      >>> load_monitor_rename_map()  # doctest: +SKIP
    """
    if yaml is None:
        raise RuntimeError("PyYAML required to load monitor_variable_rename_map.yaml")
    path = yaml_path()
    with path.open(encoding="utf-8") as fd:
        data = yaml.safe_load(fd)
    if not isinstance(data, dict):
        raise ValueError("monitor_variable_rename_map.yaml must be a mapping")
    return data


def type_renames() -> dict[str, str]:
    """
    Legacy st_name -> canonical st_name.
    
    Returns:
      dict[str, str]: dict[str, str] produced by this call.
    
    Examples:
      >>> type_renames()  # doctest: +SKIP
    """
    return dict(load_monitor_rename_map().get("types") or {})


def event_renames() -> dict[str, str]:
    """
    Legacy event key -> canonical event key.
    
    Returns:
      dict[str, str]: dict[str, str] produced by this call.
    
    Examples:
      >>> event_renames()  # doctest: +SKIP
    """
    return dict(load_monitor_rename_map().get("events") or {})


def type_event_renames() -> dict[str, dict[str, str]]:
    """
    Canonical st_name -> {legacy_event -> canonical_event} (type-scoped).
    
    Use for colliding keys (e.g. lustre_llite ``open`` / ``read``). Do **not**
    dump these into the global ``events:`` map.
    
    Returns:
      dict[str, dict[str, str]]: dict[str, dict[str, str]] produced by this
      call.
    
    Examples:
      >>> type_event_renames()  # doctest: +SKIP
    """
    raw = load_monitor_rename_map().get("type_events") or {}
    out: dict[str, dict[str, str]] = {}
    if not isinstance(raw, dict):
        return out
    for typ, mapping in raw.items():
        if not isinstance(mapping, dict):
            continue
        out[str(typ)] = {str(k): str(v) for k, v in mapping.items()}
    return out


def legacy_type_names() -> frozenset[str]:
    """
    Legacy type names.
    
    Returns:
      frozenset[str]: frozenset[str] produced by this call.
    
    Examples:
      >>> legacy_type_names()  # doctest: +SKIP
    """
    return frozenset(type_renames().keys())


def canonical_type_names() -> frozenset[str]:
    """
    Canonical type names.
    
    Returns:
      frozenset[str]: frozenset[str] produced by this call.
    
    Examples:
      >>> canonical_type_names()  # doctest: +SKIP
    """
    return frozenset(type_renames().values())


def removed_legacy_symbols() -> frozenset[str]:
    """
    Removed legacy symbols.
    
    Returns:
      frozenset[str]: frozenset[str] produced by this call.
    
    Examples:
      >>> removed_legacy_symbols()  # doctest: +SKIP
    """
    raw = load_monitor_rename_map().get("removed_legacy") or []
    return frozenset(str(x) for x in raw)
