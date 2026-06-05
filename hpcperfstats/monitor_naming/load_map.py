"""Load docs/monitor_variable_rename_map.yaml (single source of truth)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


def _repo_root() -> Path:
    # hpcperfstats/monitor_naming/load_map.py -> HPCPerfStats/
    return Path(__file__).resolve().parents[2]


def yaml_path() -> Path:
    packaged = Path(__file__).resolve().parent / "monitor_variable_rename_map.yaml"
    if packaged.is_file():
        return packaged
    return _repo_root() / "docs" / "monitor_variable_rename_map.yaml"


@lru_cache(maxsize=1)
def load_monitor_rename_map() -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML required to load monitor_variable_rename_map.yaml")
    path = yaml_path()
    with path.open(encoding="utf-8") as fd:
        data = yaml.safe_load(fd)
    if not isinstance(data, dict):
        raise ValueError("monitor_variable_rename_map.yaml must be a mapping")
    return data


def type_renames() -> dict[str, str]:
    """Legacy st_name -> canonical st_name."""
    return dict(load_monitor_rename_map().get("types") or {})


def event_renames() -> dict[str, str]:
    """Legacy event key -> canonical event key."""
    return dict(load_monitor_rename_map().get("events") or {})


def legacy_type_names() -> frozenset[str]:
    return frozenset(type_renames().keys())


def canonical_type_names() -> frozenset[str]:
    return frozenset(type_renames().values())


def removed_legacy_symbols() -> frozenset[str]:
    raw = load_monitor_rename_map().get("removed_legacy") or []
    return frozenset(str(x) for x in raw)
