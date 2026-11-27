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
  name = "/".join(raw.parts[-2:])
  assert smm.raw_path_in_sealed_members(str(raw), {name: 3})
  assert not smm.raw_path_in_sealed_members(str(raw), {name: 4})
  assert smm.sealed_member_map_census(str(tmp_path)) == {"maps": 0, "valid": 0}


# --- DE-36: ``./``-prefixed tar member names are the same file ---


def test_parse_tar_tvf_normalizes_dot_slash_member_name():
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    _parse_tar_tvf_size_and_name,
  )

  line = "-rw-r--r-- 0/0 12 2026-01-01 00:00 ./host/file"
  assert _parse_tar_tvf_size_and_name(line) == ("host/file", 12)
  bsd = "-rw-r--r--  0 0  0  7 Jan  1 00:00 ./host/file"
  assert _parse_tar_tvf_size_and_name(bsd) == ("host/file", 7)


def test_get_tar_member_name_normalizes_dot_segments():
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    get_tar_member_name,
  )

  assert get_tar_member_name("/./a/b") == "a/b"
  assert get_tar_member_name("/a/b") == "a/b"
  assert get_tar_member_name("/a//b") == "a/b"


def _write_legacy_map(sealed, entries):
  """Persist a map as an older build did (keys stored verbatim)."""
  from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    save_persistence_document,
  )

  map_path = smm.sealed_member_map_path(str(sealed))
  os.makedirs(os.path.dirname(map_path), exist_ok=True)
  save_persistence_document(
    map_path,
    smm.SEALED_MEMBER_MAP_KIND,
    {
      "sealed_path": str(sealed),
      "zst_identity": smm.archive_identity(str(sealed)),
      "entries": entries,
      "members_sha256": smm._members_sha256(entries),
      "source": "sweep",
      "written_at": 0.0,
    },
  )
  smm._CACHE.clear()


def test_sealed_members_lookup_merges_dot_slash_spellings_max_size(tmp_path):
  sealed = tmp_path / "2026-10-17.tar.zst"
  sealed.write_bytes(b"sealed")
  _write_legacy_map(sealed, {"./h/1": 9, "h/1": 4, "./h/2": 5, "h/3": 6})
  assert smm.sealed_members_lookup(str(sealed), request_rebuild=False) == {
    "h/1": 9,
    "h/2": 5,
    "h/3": 6,
  }


def test_raw_path_in_sealed_members_matches_dot_slash_spelling(tmp_path):
  raw = tmp_path / "1"
  raw.write_text("abc")
  name = str(raw).lstrip("/")
  sealed = tmp_path / "2026-10-18.tar.zst"
  sealed.write_bytes(b"sealed")
  _write_legacy_map(sealed, {"./" + name: 3})
  members = smm.sealed_members_lookup(str(sealed), request_rebuild=False)
  assert smm.raw_path_in_sealed_members(str(raw), members)
  assert smm.raw_path_in_sealed_members("/." + str(raw), members)


def test_write_sealed_member_map_normalizes_spellings(tmp_path):
  sealed = tmp_path / "2026-10-19.tar.zst"
  sealed.write_bytes(b"sealed")
  assert smm.write_sealed_member_map(
    str(sealed), {"./h/1": 2, "h/1": 8}, source="seal"
  )
  smm._CACHE.clear()
  assert smm.sealed_members_lookup(str(sealed), request_rebuild=False) == {
    "h/1": 8
  }


def _tar_with_members(tar_path, members):
  """Write ``(name, payload)`` members to ``tar_path`` in order."""
  import io
  import tarfile

  with tarfile.open(tar_path, "w") as tf:
    for name, payload in members:
      info = tarfile.TarInfo(name)
      info.size = len(payload)
      tf.addfile(info, io.BytesIO(payload))


def test_dedupe_merges_dot_slash_spellings_keeps_largest(tmp_path):
  import tarfile

  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    dedupe_tar_keep_largest_file_per_member,
    get_existing_archive_members,
    tar_has_duplicate_file_members,
  )

  tar_path = tmp_path / "2026-10-17.tar"
  _tar_with_members(tar_path, [("./p/q", b"old"), ("p/q", b"grown-copy")])
  assert tar_has_duplicate_file_members(str(tar_path))
  assert dedupe_tar_keep_largest_file_per_member(str(tar_path), log_fn=None)
  assert not tar_has_duplicate_file_members(str(tar_path))
  assert get_existing_archive_members(str(tar_path)) == {"p/q": 10}
  with tarfile.open(tar_path, "r") as tf:
    assert [m.name for m in tf.getmembers() if m.isfile()] == ["p/q"]


def test_sealed_spool_extracts_dot_slash_stored_member(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    atomic_seal_tar_to_zst,
    iter_sealed_daily_archive_member_paths,
  )

  tar_path = tmp_path / "2026-10-17.tar"
  zst_path = tmp_path / "2026-10-17.tar.zst"
  _tar_with_members(
    tar_path,
    [("./host.example/1", b"old\n"), ("./host.example/2", b"two\n")],
  )
  atomic_seal_tar_to_zst(
    str(tar_path),
    str(zst_path),
    num_threads=1,
    compress_level=3,
    keep_uncompressed_tar=False,
    log_fn=None,
  )
  spool = str(tmp_path / "spool")
  got = {}
  for name, path in iter_sealed_daily_archive_member_paths(
    str(zst_path), spool_dir=spool
  ):
    with open(path, encoding="utf-8") as fh:
      got[name] = fh.read()
  assert got == {"host.example/1": "old\n", "host.example/2": "two\n"}


def test_union_rebuild_copies_dot_slash_stored_members(tmp_path, monkeypatch):
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as ah

  tar_path = tmp_path / "2026-10-17.tar"
  zst_path = tmp_path / "2026-10-17.tar.zst"
  _tar_with_members(
    tar_path,
    [("./h/1", b"old"), ("h/1", b"grown-copy"), ("./h/2", b"two")],
  )
  monkeypatch.setattr(
    ah, "invalidate_after_daily_tar_mutation", lambda *_a, **_k: None
  )
  union = ah.get_existing_archive_members(str(tar_path))
  assert union == {"h/1": 10, "h/2": 3}
  assert ah.rebuild_daily_tar_member_union_in_place(
    str(tar_path),
    str(zst_path),
    union,
    log_fn=lambda *a, **k: None,
    stall_warn_s=9999,
    stall_yield_s=99999,
  )
  assert ah.get_existing_archive_members(str(tar_path)) == union


# --- DE-37: members compare by ``<node>/<epoch>`` whatever the prefix ---


def test_member_key_is_node_and_epoch_for_any_prefix():
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    _member_key_rows,
    _parse_tar_tvf_size_and_name,
    get_tar_member_name,
  )

  for spelling in (
    "/hpcperfstats/archive/c001.vista/1757912400",
    "./hpcperfstats/archive/c001.vista/1757912400",
    "/./hpcperfstats/archive/c001.vista/1757912400",
    "/mnt/old_root/c001.vista/1757912400",
    "c001.vista/1757912400",
  ):
    assert get_tar_member_name(spelling) == "c001.vista/1757912400"
  line = "-rw-r--r-- 0/0 9 2026-01-01 00:00 /mnt/old_root/c001.vista/17"
  parsed = _parse_tar_tvf_size_and_name(line)
  assert _member_key_rows("/d/x.tar", [parsed]) == [("c001.vista/17", 9)]


def test_raw_path_in_sealed_members_matches_absolute_prefix_spelling(tmp_path):
  raw = tmp_path / "c001.vista" / "17"
  raw.parent.mkdir()
  raw.write_text("abc")
  sealed = tmp_path / "2026-10-20.tar.zst"
  sealed.write_bytes(b"sealed")
  _write_legacy_map(sealed, {"/mnt/old_root/c001.vista/17": 3})
  members = smm.sealed_members_lookup(str(sealed), request_rebuild=False)
  assert members == {"c001.vista/17": 3}
  assert smm.raw_path_in_sealed_members(str(raw), members)


def test_extract_by_key_matches_whole_components_only(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    _gnu_tar_extract_named_member,
  )

  tar_path = tmp_path / "2026-10-21.tar"
  _tar_with_members(tar_path, [("r/xh/1", b"other")])
  out = tmp_path / "out"
  out.mkdir()
  assert not _gnu_tar_extract_named_member(str(tar_path), "h/1", str(out))
  assert not any(out.rglob("*1"))


def test_sealed_spool_extracts_largest_copy_across_prefixes(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    atomic_seal_tar_to_zst,
    iter_sealed_daily_archive_member_paths,
  )

  tar_path = tmp_path / "2026-10-22.tar"
  zst_path = tmp_path / "2026-10-22.tar.zst"
  _tar_with_members(
    tar_path,
    [
      ("./hpcperfstats/archive/h/1", b"old\n"),
      ("/mnt/old_root/h/1", b"grown copy\n"),
      ("hpcperfstats/archive/xh/1", b"other\n"),
    ],
  )
  atomic_seal_tar_to_zst(
    str(tar_path),
    str(zst_path),
    num_threads=1,
    compress_level=3,
    keep_uncompressed_tar=False,
    log_fn=None,
  )
  got = {}
  for name, path in iter_sealed_daily_archive_member_paths(
    str(zst_path), spool_dir=str(tmp_path / "spool")
  ):
    with open(path, encoding="utf-8") as fh:
      got[name] = fh.read()
  assert got == {"h/1": "grown copy\n", "xh/1": "other\n"}


def test_member_key_collision_warns_once_per_archive(tmp_path, monkeypatch):
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as ah

  lines = []
  monkeypatch.setattr(ah, "log_print", lambda msg, **_k: lines.append(msg))
  tar_path = tmp_path / "2026-10-23.tar"
  _tar_with_members(
    tar_path,
    [
      ("./hpcperfstats/archive/h/1", b"a"),
      ("hpcperfstats/archive/h/1", b"bb"),
      ("mnt/old_root/h/1", b"ccc"),
    ],
  )
  for _ in range(2):
    assert ah._read_tar_file_member_sizes_unlocked(str(tar_path)) == {"h/1": 3}
  warns = [line for line in lines if "member key collision" in line]
  assert len(warns) == 1, lines
  assert "hpcperfstats/archive/h/1" in warns[0]
  assert "mnt/old_root/h/1" in warns[0]


def test_members_store_reload_rekeys_full_name_day_files(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
    SyncTimedbArchiveMembersStore,
  )
  from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    save_persistence_document,
  )

  store = SyncTimedbArchiveMembersStore(str(tmp_path))
  path = store._day_path("2026-10-17")
  os.makedirs(os.path.dirname(path), exist_ok=True)
  save_persistence_document(
    path,
    "archive_members_day",
    {
      "schema_version": 1,
      "day_token": "2026-10-17",
      "identities": {
        "ident": {
          "members": {
            "./hpcperfstats/archive/h/1": 4,
            "hpcperfstats/archive/h/1": 9,
          },
          "complete": True,
        }
      },
    },
  )
  reloaded = SyncTimedbArchiveMembersStore(str(tmp_path))
  assert reloaded.lookup_complete_map("2026-10-17", "ident") == {"h/1": 9}
