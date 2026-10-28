"""Tests for the generalized write-behind sidecar helpers."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from hpcperfstats.dbload.lib import sync_timedb_write_behind as wb
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
  load_persistence_document,
)

KIND = "file_complete_ingest_mark"


@pytest.fixture(autouse=True)
def _clean_registry():
  wb.reset_write_behind_for_tests()
  yield
  wb.reset_write_behind_for_tests()


def _loader(path: str) -> dict:
  raw = load_persistence_document(path, KIND, default={"entries": {}})
  entries = raw.get("entries") if isinstance(raw, dict) else None
  return dict(entries) if isinstance(entries, dict) else {}


def _store(tmp_path: Path, **kw) -> wb.WriteBehindEntryStore:
  return wb.get_entry_store(
    str(tmp_path / "marks.json"),
    KIND,
    schema_version=1,
    load_entries=_loader,
    interval_s=kw.pop("interval_s", 3600.0),
    **kw,
  )


def _on_disk(tmp_path: Path) -> dict:
  return _loader(str(tmp_path / "marks.json"))


# --- WriteBehindWriter ------------------------------------------------------


def test_writer_coalesces_dirty_notifications_into_one_write():
  calls = []
  writer = wb.WriteBehindWriter(
    name="t-coalesce",
    write_fn=lambda force: calls.append(force) or True,
    interval_s=0.05,
  )
  writer.start()
  try:
    for _ in range(200):
      writer.mark_dirty()
    deadline = time.monotonic() + 3.0
    while not calls and time.monotonic() < deadline:
      time.sleep(0.01)
    time.sleep(0.2)
  finally:
    writer.stop()
  assert 1 <= len(calls) <= 3
  assert writer.dirty is False


def test_writer_does_not_write_when_clean():
  calls = []
  writer = wb.WriteBehindWriter(
    name="t-clean", write_fn=lambda force: calls.append(force), interval_s=0.01
  )
  writer.start()
  time.sleep(0.2)
  writer.stop()
  assert calls == []


def test_writer_force_flush_waits_and_returns():
  calls = []
  writer = wb.WriteBehindWriter(
    name="t-force",
    write_fn=lambda force: calls.append(force),
    interval_s=3600.0,
  )
  writer.start()
  try:
    writer.mark_dirty()
    writer.flush()
    assert calls == [True]
    assert writer.dirty is False
  finally:
    writer.stop()


def test_writer_force_flush_raises_write_error_and_stays_dirty():
  def boom(force: bool) -> None:
    raise OSError("disk full")

  writer = wb.WriteBehindWriter(name="t-err", write_fn=boom, interval_s=3600.0)
  writer.start()
  try:
    writer.mark_dirty()
    with pytest.raises(OSError, match="disk full"):
      writer.flush()
    assert writer.dirty is True
  finally:
    writer.stop(final_flush=False)


def test_writer_stop_flushes_pending_dirty():
  calls = []
  writer = wb.WriteBehindWriter(
    name="t-stop", write_fn=lambda force: calls.append(force), interval_s=3600.0
  )
  writer.start()
  writer.mark_dirty()
  writer.stop()
  assert len(calls) == 1


def test_writer_flush_without_thread_raises():
  writer = wb.WriteBehindWriter(
    name="t-nothread", write_fn=lambda force: None, interval_s=1.0
  )
  with pytest.raises(RuntimeError):
    writer.flush()


def test_writer_thread_is_named_and_daemon():
  seen = {}
  writer = wb.WriteBehindWriter(
    name="my-persist-thread",
    write_fn=lambda force: seen.setdefault("t", threading.current_thread()),
    interval_s=3600.0,
  )
  writer.start()
  try:
    writer.mark_dirty()
    writer.flush()
  finally:
    writer.stop()
  assert seen["t"].name == "my-persist-thread"
  assert seen["t"].daemon is True


# --- WriteBehindEntryStore --------------------------------------------------


def test_set_does_no_disk_io_until_flush(tmp_path, monkeypatch):
  saves = []
  real = wb.save_persistence_document
  monkeypatch.setattr(
    wb,
    "save_persistence_document",
    lambda *a, **k: saves.append(a) or real(*a, **k),
  )
  store = _store(tmp_path)
  for i in range(500):
    store.set(f"k{i}", {"i": i})
  assert saves == []
  assert store.contains("k499")
  assert _on_disk(tmp_path) == {}
  store.flush()
  assert len(saves) == 1
  assert len(_on_disk(tmp_path)) == 500


def test_contains_and_get_do_not_copy_or_reload(tmp_path, monkeypatch):
  store = _store(tmp_path)
  store.set("a", {"v": 1})
  store.flush()
  loads = {"n": 0}
  real = wb.load_persistence_document
  monkeypatch.setattr(
    wb,
    "load_persistence_document",
    lambda *a, **k: loads.__setitem__("n", loads["n"] + 1) or real(*a, **k),
  )
  for _ in range(1000):
    assert store.contains("a")
    assert store.get("a") == {"v": 1}
    assert not store.contains("nope")
  assert loads["n"] == 0


def test_lazy_load_reads_existing_file(tmp_path):
  path = tmp_path / "marks.json"
  path.write_text(json.dumps({"schema_version": 1, "entries": {"x": {"v": 9}}}))
  store = _store(tmp_path)
  assert store.contains("x")
  assert store.get("x") == {"v": 9}


def test_flushed_content_survives_a_fresh_process(tmp_path):
  store = _store(tmp_path)
  store.set("a", {"v": 1})
  store.flush()
  wb.reset_write_behind_for_tests()
  again = _store(tmp_path)
  assert again.contains("a")


def test_unflushed_content_is_lost_on_crash_but_flushed_is_kept(tmp_path):
  store = _store(tmp_path)
  store.set("kept", {"v": 1})
  store.flush()
  store.set("lost", {"v": 2})
  wb.reset_write_behind_for_tests(flush=False)  # simulate os._exit
  again = _store(tmp_path)
  assert again.contains("kept")
  assert not again.contains("lost")


def test_remove_if_is_durable_immediately(tmp_path):
  store = _store(tmp_path)
  store.set("a", {"p": "/x"})
  store.set("b", {"p": "/y"})
  store.flush()
  removed = store.remove_if(lambda k, v: v.get("p") == "/x")
  assert removed == 1
  assert set(_on_disk(tmp_path)) == {"b"}  # no flush call needed


def test_removed_key_is_not_resurrected_by_merge_with_stale_disk(tmp_path):
  store = _store(tmp_path)
  store.set("a", {"v": 1})
  store.set("b", {"v": 2})
  store.flush()
  path = tmp_path / "marks.json"
  path.write_text(
    json.dumps(
      {
        "schema_version": 1,
        "entries": {"a": {"v": 1}, "b": {"v": 2}, "c": {"v": 3}},
      }
    )
  )
  store.remove_if(lambda k, v: k == "a")
  assert set(_on_disk(tmp_path)) == {"b", "c"}  # c kept (merge), a stays gone


def test_flush_merges_entries_written_by_another_process(tmp_path):
  store = _store(tmp_path)
  store.set("mine", {"v": 1})
  store.flush()
  path = tmp_path / "marks.json"
  disk = json.loads(path.read_text())
  disk["entries"]["theirs"] = {"v": 2}
  path.write_text(json.dumps(disk))
  store.set("mine2", {"v": 3})
  store.flush()
  assert set(_on_disk(tmp_path)) == {"mine", "mine2", "theirs"}
  assert store.contains("theirs")  # adopted into memory too


def test_concurrent_sets_from_many_threads_all_persist(tmp_path):
  store = _store(tmp_path)

  def worker(n: int) -> None:
    for i in range(200):
      store.set(f"t{n}-{i}", {"i": i})

  threads = [threading.Thread(target=worker, args=(n,)) for n in range(32)]
  for t in threads:
    t.start()
  for t in threads:
    t.join()
  store.flush()
  assert len(_on_disk(tmp_path)) == 32 * 200


def test_slow_disk_write_does_not_block_set_or_contains(tmp_path, monkeypatch):
  store = _store(tmp_path)
  store.set("seed", {"v": 0})
  started = threading.Event()
  release = threading.Event()
  real = wb.save_persistence_document

  def slow_save(*a, **k):
    started.set()
    release.wait(5.0)
    return real(*a, **k)

  monkeypatch.setattr(wb, "save_persistence_document", slow_save)
  flusher = threading.Thread(target=store.flush)
  flusher.start()
  assert started.wait(3.0)
  t0 = time.monotonic()
  for i in range(2000):
    store.set(f"during{i}", {"i": i})
    assert store.contains("seed")
  assert time.monotonic() - t0 < 1.0
  release.set()
  flusher.join(5.0)
  store.flush()
  assert len(_on_disk(tmp_path)) == 2001


def test_periodic_flush_writes_without_explicit_call(tmp_path):
  store = _store(tmp_path, interval_s=0.05)
  store.set("auto", {"v": 1})
  deadline = time.monotonic() + 3.0
  while "auto" not in _on_disk(tmp_path) and time.monotonic() < deadline:
    time.sleep(0.02)
  assert "auto" in _on_disk(tmp_path)


def test_registry_returns_same_store_per_path_and_flush_all(tmp_path):
  a = _store(tmp_path)
  assert _store(tmp_path) is a
  a.set("z", {"v": 1})
  wb.flush_all_write_behind()
  assert "z" in _on_disk(tmp_path)


def test_replace_all_is_durable_and_replaces(tmp_path):
  store = _store(tmp_path)
  store.set("old", {"v": 1})
  store.flush()
  store.replace_all({"new": {"v": 2}})
  assert set(_on_disk(tmp_path)) == {"new"}
  assert not store.contains("old")


def test_malformed_payload_loads_as_empty(tmp_path):
  (tmp_path / "marks.json").write_text(json.dumps([{"not": "an envelope"}]))
  store = _store(tmp_path)
  assert not store.contains("anything")
  assert store.snapshot() == {}


def test_two_thousand_sets_on_big_store_stay_fast_and_write_once(
  tmp_path, monkeypatch
):
  """Regression guard for the O(N)-per-mark rewrite (1.5 s per file on 42 MB)."""
  store = _store(tmp_path)
  for i in range(50_000):
    store.set(f"seed{i}", {"path": f"/a/{i}", "mtime": i, "size": i})
  store.flush()
  saves = []
  real = wb.save_persistence_document
  monkeypatch.setattr(
    wb,
    "save_persistence_document",
    lambda *a, **k: saves.append(1) or real(*a, **k),
  )
  t0 = time.monotonic()
  for i in range(2_000):
    store.set(f"new{i}", {"path": f"/b/{i}", "mtime": i, "size": i})
    store.contains(f"seed{i}")
  elapsed = time.monotonic() - t0
  store.flush()
  assert elapsed < 1.0
  assert len(saves) == 1
