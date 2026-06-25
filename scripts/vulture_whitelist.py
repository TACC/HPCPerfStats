"""Vulture whitelist: intentional stubs and framework entrypoints.

Run: vulture hpcperfstats scripts/vulture_whitelist.py --min-confidence 80
"""
from __future__ import annotations

# Janitor-only barrier stubs in sync_timedb (supervisor must not call).
from hpcperfstats.dbload import sync_timedb as _st

_JANITOR_ONLY_STUBS = (
    _st.seal_dirty_daily_archives,
    _st.remove_verified_archived_raw_files,
    _st.remove_verified_uncompressed_daily_tars,
)
