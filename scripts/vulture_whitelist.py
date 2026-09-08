"""
Vulture whitelist: intentional entrypoints.

Run: vulture hpcperfstats scripts/vulture_whitelist.py --min-confidence 80

Attributes:
  _JANITOR_ENTRYPOINTS: Attribute.
"""
from __future__ import annotations

from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as _ah

_JANITOR_ENTRYPOINTS = (
    _ah.seal_dirty_daily_archives,
    _ah.remove_verified_archived_raw_files,
    _ah.remove_verified_uncompressed_daily_tars,
)
