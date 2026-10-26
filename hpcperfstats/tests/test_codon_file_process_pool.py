"""Codon file-owned parse runs in worker processes (no shared Boehm GC lock).

Eight threads in one Codon runtime parsed 2.5x slower per file than one thread
(Boehm allocation-lock contention); separate processes scale linearly. These
tests run the real ``codon_process_worker`` subprocess against an importable
fake pyext.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from hpcperfstats.dbload.lib import (
  codon_process_worker as w,
  sync_timedb_codon_bridge as bridge,
)

FAKE = "hpcperfstats.tests.codon_worker_fake_mod"


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch):
  monkeypatch.setenv(bridge.CODON_EXT_ENV, FAKE)
  monkeypatch.setattr(bridge, "_use_codon_file_process_pool", lambda: True)
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.conf_parser.get_sync_codon_worker_processes",
    lambda: 2,
  )
  bridge.reset_codon_bridge_for_tests()
  bridge._configure_slot_pool(4)
  yield
  bridge.reset_codon_bridge_for_tests()


def _frames(path: Path, **kw: Any) -> list[dict[str, Any]]:
  return list(bridge._iter_codon_parse_file_frames(str(path), 1024, **kw))


def test_copy_frame_wire_roundtrip() -> None:
  body = {
    "stats": {"host_copy": b"h\tx\n", "proc_copy": b"p\n"},
    "procs": {},
    "marks": [],
    "eof": True,
    "copy_unique": True,
    "file_session": True,
  }
  out = w.decode_response("parse", w.encode_response_ok("parse", body))
  assert out["stats"] == {"host_copy": b"h\tx\n", "proc_copy": b"p\n"}
  assert (
    out["procs"] == {} and out["eof"] is True and out["copy_unique"] is True
  )


def test_soa_frame_wire_unchanged_by_copy_flag() -> None:
  out = w.decode_response(
    "parse", w.encode_response_ok("parse", {"stats": [], "procs": []})
  )
  assert "host_copy" not in out["stats"]


def test_parse_file_request_carries_emit_insert_only_when_set() -> None:
  _k, env = w.decode_request(w.encode_parse_file_request("/x", 1))
  assert "emit_insert" not in env
  _k, env = w.decode_request(w.encode_parse_file_request("/x", 1, 0, None, 1))
  assert env["emit_insert"] == 1


def test_process_worker_streams_copy_frames_with_exclude(
  pool, tmp_path: Path
) -> None:
  path = tmp_path / "stats"
  path.write_text("a\nb\n", encoding="utf-8")
  frames = _frames(path, exclude=["host_vfs", "host_ps"])
  bodies = [f["stats"]["host_copy"].decode().split("|") for f in frames]
  assert [b[3] for b in bodies] == ["a", "b"]
  assert {b[1] for b in bodies} == {"host_vfs,host_ps"}
  assert frames[-1]["eof"] is True and frames[0]["eof"] is False
  import os

  assert {b[0] for b in bodies} != {str(os.getpid())}


def test_empty_file_yields_no_frames_and_does_not_hang(
  pool, tmp_path: Path
) -> None:
  path = tmp_path / "empty"
  path.write_text("", encoding="utf-8")
  assert _frames(path) == []


def test_abandoned_stream_kills_worker_and_pool_recovers(
  pool, tmp_path: Path
) -> None:
  path = tmp_path / "stats"
  path.write_text("a\nb\nc\n", encoding="utf-8")
  gen = bridge._iter_codon_parse_file_frames(str(path), 1024)
  next(gen)
  gen.close()
  assert [
    f["stats"]["host_copy"].decode().split("|")[3] for f in _frames(path)
  ] == [
    "a",
    "b",
    "c",
  ]


def test_concurrent_files_use_distinct_worker_processes(
  pool, tmp_path: Path
) -> None:
  path = tmp_path / "stats"
  path.write_text(
    "\n".join("x" * 5 for _ in range(300)) + "\n", encoding="utf-8"
  )
  pids: list[set[str]] = []
  start = threading.Barrier(2)

  def run() -> None:
    start.wait()
    pids.append(
      {f["stats"]["host_copy"].decode().split("|")[0] for f in _frames(path)}
    )

  ts = [threading.Thread(target=run) for _ in range(2)]
  [t.start() for t in ts]
  [t.join() for t in ts]
  assert len(pids) == 2 and all(len(p) == 1 for p in pids)
  assert pids[0] != pids[1]


def test_file_process_pool_off_for_fake_module(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv(bridge.CODON_EXT_ENV, FAKE)
  assert bridge._use_codon_file_process_pool() is False


def test_worker_process_count_default_is_64() -> None:
  from hpcperfstats.dbload.lib import conf_parser as cfg

  assert cfg.INI_OPTION_DEFAULTS["sync_codon_worker_processes"] == "64"


def test_worker_process_count_from_ini(monkeypatch: pytest.MonkeyPatch) -> None:
  from hpcperfstats.dbload.lib import conf_parser as cfg

  monkeypatch.setattr(cfg, "_ensure_cfg_loaded", lambda: None)
  monkeypatch.setattr(cfg, "get_effective_cores", lambda: 28)
  monkeypatch.setattr(cfg, "get_sync_ingest_pool_processes", lambda: 64)
  monkeypatch.setattr(cfg, "_pipeline_getint", lambda _k: 0)
  assert cfg.get_sync_codon_worker_processes() == 7
  monkeypatch.setattr(cfg, "_pipeline_getint", lambda _k: 200)
  assert cfg.get_sync_codon_worker_processes() == 64
  monkeypatch.setattr(cfg, "get_sync_ingest_pool_processes", lambda: 4)
  monkeypatch.setattr(cfg, "_pipeline_getint", lambda _k: 0)
  assert cfg.get_sync_codon_worker_processes() == 4


def test_frame_larger_than_pipe_buffer_is_read_in_full(
  pool, tmp_path: Path
) -> None:
  """Regression: unbuffered pipe ``read(n)`` returned short -> 'codon worker EOF'."""
  path = tmp_path / "stats"
  path.write_text("big\nsmall\n", encoding="utf-8")
  frames = _frames(path)
  assert [len(f["stats"]["host_copy"]) > 300_000 for f in frames] == [
    True,
    False,
  ]


def test_codon_file_stream_flush_window_is_capped(
  monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
  """A 512 MiB flush must reach Codon as the 1 MiB window cap."""
  from hpcperfstats.dbload.lib import conf_parser as cfg
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    parse_stats_file_streaming_incremental,
  )

  seen: list[int] = []
  monkeypatch.setattr(bridge, "codon_ingest_parse_enabled", lambda: True)
  monkeypatch.setattr(
    bridge,
    "try_codon_parse_file_stream",
    lambda path, flush, carry, on_chunk, exclude=None, coalesce_rows=1: (
      seen.append((flush, coalesce_rows))
    ),
  )
  monkeypatch.setattr(cfg, "get_sync_codon_parse_flush_max_mib", lambda: 1)
  path = tmp_path / "stats"
  path.write_text("!cpu user,W=64,U=1\n1000.0 J1 h\ncpu 0 @full 1.0\n")
  parse_stats_file_streaming_incremental(
    str(path), flush_max_bytes=512 << 20, on_chunk=lambda s, p: None
  )
  assert seen == [(1 << 20, 10_000)]


def _pid_of(path: Path) -> str:
  return _frames(path)[0]["stats"]["host_copy"].decode().split("|")[0]


def test_worker_recycled_between_files_when_rss_over_limit(
  pool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """Boehm heaps only grow, so an oversized worker is replaced after its file."""
  from hpcperfstats.dbload.lib import conf_parser as cfg

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.conf_parser.get_sync_codon_parse_flush_max_mib",
    lambda: 16,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.conf_parser.get_sync_codon_worker_processes",
    lambda: 1,
  )
  bridge.reset_codon_bridge_for_tests()
  bridge._configure_slot_pool(2)
  path = tmp_path / "stats"
  path.write_text("a\n", encoding="utf-8")
  monkeypatch.setattr(
    cfg, "get_sync_codon_worker_recycle_rss_mib", lambda: 1 << 20
  )
  first, second = _pid_of(path), _pid_of(path)
  assert first == second
  monkeypatch.setattr(cfg, "get_sync_codon_worker_recycle_rss_mib", lambda: 1)
  third = _pid_of(path)
  fourth = _pid_of(path)
  assert third == second and fourth != third


def test_worker_rss_unreadable_does_not_recycle() -> None:
  class _Gone:
    pid = 2**22 + 12345

  assert bridge._worker_rss_over_limit(_Gone()) is False  # type: ignore[arg-type]


def test_copy_bytes_are_written_in_row_slices(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Whole-payload COPY+INSERT stalled writers; CPython path uses 10k-row slices."""
  from hpcperfstats.dbload import sync_timedb as st

  body = b"".join(b"%d\tx\n" % i for i in range(25))
  assert list(st._copy_bytes_slices(b"", 10)) == []
  parts = list(st._copy_bytes_slices(body, 10))
  assert [p.count(b"\n") for p in parts] == [10, 10, 5] and b"".join(
    parts
  ) == body
  assert list(st._copy_bytes_slices(b"no-newline", 10)) == [b"no-newline"]
  host, proc = [], []
  monkeypatch.setattr(st, "_ingest_write_frame_slice_rows", lambda: 10)
  monkeypatch.setattr(st, "_time_ingest_db_reconnect", lambda: None)
  monkeypatch.setattr(st, "update_worker_substage", lambda *_a: None)
  monkeypatch.setattr(st, "_write_host_copy_payload", host.append)
  monkeypatch.setattr(st, "_write_proc_copy_payload", proc.append)
  st._write_stats_payload_to_db(
    "f", {"host_copy": body, "proc_copy": b"p\n"}, None, True, copy_unique=True
  )
  assert [h.count(b"\n") for h in host] == [10, 10, 5] and proc == [b"p\n"]


def test_worker_mem_line_has_rss_split_and_extra_fields() -> None:
  line = w.format_worker_mem_line("win", file="f", win=3, host_kib=7)
  fields = dict(kv.split("=") for kv in line.split()[1:])
  assert line.startswith("codon_worker_mem tag=win")
  assert (
    fields["file"] == "f" and fields["win"] == "3" and fields["host_kib"] == "7"
  )
  assert int(fields["pid"]) > 0
  assert int(fields["rss"]) >= int(fields["rss_anon"]) >= 0


def test_worker_streams_mem_lines_to_stderr_when_enabled(
  pool,
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  capfd: pytest.CaptureFixture,
) -> None:
  monkeypatch.setenv(w.WORKER_MEM_ENV, "1")
  bridge.reset_codon_bridge_for_tests()
  bridge._configure_slot_pool(2)
  path = tmp_path / "stats"
  path.write_text("a\nb\n", encoding="utf-8")
  assert len(_frames(path)) == 2
  err = capfd.readouterr().err
  tags = [
    ln.split()[1]
    for ln in err.splitlines()
    if ln.startswith("codon_worker_mem")
  ]
  assert tags == ["tag=open", "tag=win", "tag=win", "tag=eof"]


def _cf(
  host: bytes, eof: bool, marks: list | None = None, proc: bytes = b""
) -> dict:
  return {
    "stats": {"host_copy": host, "proc_copy": proc},
    "procs": {},
    "marks": marks or [],
    "eof": eof,
    "copy_unique": True,
  }


def test_coalesce_host_rows_by_row_count_never_merges_proc() -> None:
  """Tiny windows merge up to N rows; proc stays one payload per window."""
  frames = [
    _cf(b"a\nb\n", False, [1], b"p1\n"),
    _cf(b"c\n", False, [2], b"p2\n"),
  ]
  frames += [_cf(b"d\ne\n", True)]
  out = list(bridge._coalesce_host_rows(frames, 3))
  hosts = [f["stats"]["host_copy"] for f in out if f["stats"]["host_copy"]]
  procs = [f["stats"]["proc_copy"] for f in out if f["stats"]["proc_copy"]]
  assert hosts == [b"a\nb\nc\n", b"d\ne\n"] and procs == [b"p1\n", b"p2\n"]
  assert all(
    not (f["stats"]["host_copy"] and f["stats"]["proc_copy"]) for f in out
  )
  merged = next(f for f in out if f["stats"]["host_copy"] == b"a\nb\nc\n")
  assert merged["marks"] == [1, 2]
  assert out[-1]["eof"] is True and sum(1 for f in out if f["eof"]) == 1


def test_coalesce_host_rows_flushes_partial_at_eof_and_passes_others() -> None:
  frames = [_cf(b"a\n", False), _cf(b"b\n", True)]
  out = list(bridge._coalesce_host_rows(frames, 1000))
  assert [f["stats"]["host_copy"] for f in out] == [b"a\nb\n"] and out[0]["eof"]
  assert list(bridge._coalesce_host_rows(frames, 1)) == frames
  soa = {"stats": {"time": [1.0]}, "eof": False}
  mixed = list(bridge._coalesce_host_rows([_cf(b"a\n", False), soa], 1000))
  assert [f["stats"] for f in mixed] == [
    {"host_copy": b"a\n", "proc_copy": b""},
    {"time": [1.0]},
  ]
