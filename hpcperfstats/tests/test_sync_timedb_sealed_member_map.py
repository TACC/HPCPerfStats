"""Unit tests for sync_timedb_sealed_member_map (T0 sealed member lookups)."""

from __future__ import annotations

import os

from hpcperfstats.dbload.lib import sync_timedb_sealed_member_map as smm


def test_write_then_lookup_and_identity_invalidation(tmp_path):
  sealed = tmp_path / "2026-01-01.tar.zst"
  sealed.write_bytes(b"sealed")
  assert smm.write_sealed_member_map(str(sealed), {"h/1": 3}, source="seal")
  smm._CACHE.clear()
  assert smm.sealed_members_lookup(str(sealed), request_rebuild=False) == {
    "h/1": 3
  }
  st = os.stat(sealed)
  os.utime(sealed, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
  assert smm.sealed_members_lookup(str(sealed), request_rebuild=False) is None


def test_write_skipped_when_file_changed_since_read(tmp_path):
  sealed = tmp_path / "2026-01-02.tar.zst"
  sealed.write_bytes(b"a")
  before = smm.archive_identity(str(sealed))
  sealed.write_bytes(b"bb")
  assert not smm.write_sealed_member_map(
    str(sealed), {"x": 1}, source="rebuild", identity=before
  )


def test_raw_path_membership_requires_same_size(tmp_path):
  raw = tmp_path / "1"
  raw.write_text("abc")
  name = str(raw).lstrip("/")
  assert smm.raw_path_in_sealed_members(str(raw), {name: 3})
  assert not smm.raw_path_in_sealed_members(str(raw), {name: 4})
  assert smm.sealed_member_map_census(str(tmp_path)) == {"maps": 0, "valid": 0}
