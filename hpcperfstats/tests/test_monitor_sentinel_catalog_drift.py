"""Drift guard: hpcperfstats.lib.monitor_sentinel mirrors monitor/src/stats_sentinel.h.

HPCPERFSTATS_SENTINEL_HEADER overrides the header path (used to prove the test
fails on a planted entry). The consumer image does not ship monitor/, so the
test skips there when the header is absent.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from hpcperfstats.lib import monitor_sentinel as ms

_HEADER = Path(
  os.environ.get(
    "HPCPERFSTATS_SENTINEL_HEADER",
    Path(__file__).resolve().parents[2] / "monitor/src/stats_sentinel.h",
  )
)
_ENTRY = re.compile(
  r'^\s*X\(\s*(\d+)\s*,\s*([A-Z_]+)\s*,\s*"([^"]+)"\s*\)', re.MULTILINE
)


def _header_text() -> str:
  if not _HEADER.is_file():
    pytest.skip(f"monitor header not available: {_HEADER}")
  return _HEADER.read_text()


def _header_catalog() -> dict[int, tuple[str, str]]:
  return {
    -int(n): (slug, desc) for n, slug, desc in _ENTRY.findall(_header_text())
  }


def test_python_catalog_equals_c_header():
  header = _header_catalog()
  assert header, "no X(n, SLUG, desc) entries parsed from the header"
  mirror = {c: (e.slug, e.description) for c, e in ms.CATALOG.items()}
  assert mirror == header


def test_catalog_version_matches_header():
  m = re.search(
    r"#define\s+STATS_SENTINEL_CATALOG_VERSION\s+(\d+)", _header_text()
  )
  assert m is not None
  assert int(m.group(1)) == ms.CATALOG_VERSION


def test_codes_contiguous_negative_from_minus_one():
  assert sorted(ms.CATALOG, reverse=True) == list(
    range(-1, -len(ms.CATALOG) - 1, -1)
  )


def test_every_code_has_consumer_attributes():
  for code, entry in ms.CATALOG.items():
    assert entry.code == code
    assert isinstance(entry.bridge, bool)
    assert entry.kind in {"static", "transient"}
    assert entry.description == entry.description.strip()


def test_researcher_visible_codes_are_catalog_codes():
  assert set(ms.CATALOG) >= ms.RESEARCHER_VISIBLE_CODES


def test_lookup_rejects_real_values():
  with pytest.raises(ValueError):
    ms.lookup(0)
