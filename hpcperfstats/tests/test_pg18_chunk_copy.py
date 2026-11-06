"""Unit tests for scripts/pg18_host_data_chunk_copy.py helpers."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest


def _load_mod():
  import sys

  path = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "pg18_host_data_chunk_copy.py"
  )
  name = "pg18_host_data_chunk_copy"
  spec = importlib.util.spec_from_file_location(name, path)
  assert spec and spec.loader
  mod = importlib.util.module_from_spec(spec)
  sys.modules[name] = mod
  spec.loader.exec_module(mod)
  return mod


def test_filter_chunks_by_watermark_excludes_hot_window() -> None:
  mod = _load_mod()
  wm = datetime(2026, 9, 1, tzinfo=UTC)
  cold = mod.ChunkRow(
    "_timescaledb_internal",
    "_hyper_1_1_chunk",
    datetime(2026, 8, 1, tzinfo=UTC),
    datetime(2026, 8, 2, tzinfo=UTC),
    False,
  )
  hot = mod.ChunkRow(
    "_timescaledb_internal",
    "_hyper_1_99_chunk",
    datetime(2026, 8, 30, tzinfo=UTC),
    datetime(2026, 9, 2, tzinfo=UTC),
    True,
  )
  selected = mod.filter_chunks_by_watermark([cold, hot], watermark=wm)
  assert selected == [cold]


def test_build_copy_out_refuses_parent_hypertable() -> None:
  mod = _load_mod()
  parent = mod.ChunkRow(
    "public",
    "host_data",
    datetime(2026, 1, 1, tzinfo=UTC),
    datetime(2026, 1, 2, tzinfo=UTC),
    False,
  )
  with pytest.raises(ValueError, match="parent hypertable"):
    mod.build_copy_out_sql(parent, ["time"])


def test_build_delete_range_and_copy_sql_for_retry() -> None:
  mod = _load_mod()
  chunk = mod.ChunkRow(
    "_timescaledb_internal",
    "_hyper_1_2_chunk",
    datetime(2026, 8, 10, tzinfo=UTC),
    datetime(2026, 8, 11, tzinfo=UTC),
    False,
  )
  delete_sql = mod.build_delete_range_sql(chunk)
  assert "DELETE FROM host_data" in delete_sql
  assert "time >=" in delete_sql and "time <" in delete_sql
  assert "2026-08-10" in delete_sql and "2026-08-11" in delete_sql
  out_sql = mod.build_copy_out_sql(chunk, ["time", "host"])
  assert out_sql == (
    'COPY (SELECT "time", "host" FROM _timescaledb_internal._hyper_1_2_chunk)'
    " TO STDOUT (FORMAT binary)"
  )
  assert "host_data" not in out_sql.split("FROM", 1)[1]
  assert mod.build_copy_in_sql(["time", "host"]) == (
    'COPY host_data ("time", "host") FROM STDIN (FORMAT binary)'
  )


def test_parse_chunk_tsv_and_watermark_days() -> None:
  mod = _load_mod()
  lines = [
    "_timescaledb_internal|_hyper_1_3_chunk|2026-07-01 00:00:00+00|2026-07-02 00:00:00+00|t",
  ]
  rows = mod.parse_chunk_tsv(lines)
  assert len(rows) == 1
  assert rows[0].is_compressed is True
  assert rows[0].range_end.day == 2
  now = datetime(2026, 9, 4, tzinfo=UTC)
  wm = mod.watermark_from_now(days=3, now=now)
  assert wm == datetime(2026, 9, 1, tzinfo=UTC)


def test_fetch_source_chunks_uses_psycopg_not_psql_binary(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Regression: web image has no ``psql``; catalog fetch must use psycopg."""
  mod = _load_mod()

  def _forbid_subprocess(*_a: object, **_k: object) -> None:
    raise AssertionError("subprocess must not invoke psql for catalog fetch")

  monkeypatch.setattr(mod.subprocess, "run", _forbid_subprocess)
  monkeypatch.setattr(mod.subprocess, "Popen", _forbid_subprocess)

  class _FakeCursor:
    def __enter__(self) -> _FakeCursor:
      return self

    def __exit__(self, *_a: object) -> None:
      return None

    def execute(self, _sql: str) -> None:
      return None

    def fetchall(
      self,
    ) -> list[tuple[str, str, datetime, datetime, bool]]:
      return [
        (
          "_timescaledb_internal",
          "_hyper_1_1_chunk",
          datetime(2026, 8, 1, tzinfo=UTC),
          datetime(2026, 8, 2, tzinfo=UTC),
          False,
        )
      ]

  class _FakeConn:
    def __enter__(self) -> _FakeConn:
      return self

    def __exit__(self, *_a: object) -> None:
      return None

    def cursor(self) -> _FakeCursor:
      return _FakeCursor()

    def close(self) -> None:
      return None

  monkeypatch.setattr(mod, "connect_pg", lambda **_kw: _FakeConn())
  rows = mod.fetch_source_chunks(host="db", port=5432, user="u", database="d")
  assert len(rows) == 1
  assert rows[0].chunk_name == "_hyper_1_1_chunk"
  assert '"psql"' not in Path(mod.__file__).read_text(encoding="utf-8")
  assert "'psql'" not in Path(mod.__file__).read_text(encoding="utf-8")


def test_chunk_row_counts_match_helper() -> None:
  mod = _load_mod()
  assert mod.chunk_row_counts_match(0, 0) is True
  assert mod.chunk_row_counts_match(10, 10) is True
  assert mod.chunk_row_counts_match(10, 9) is False
  assert mod.chunk_row_counts_match(0, 1) is False
  # Unknown counts (-1) must never look synced (would skip real work).
  assert mod.chunk_row_counts_match(-1, -1) is False
  assert mod.chunk_row_counts_match(-1, 0) is False
  assert mod.chunk_row_counts_match(5, -1) is False


def _opts(mod: object, **kw: object) -> object:
  base: dict[str, object] = {
    "source_host": "db",
    "target_host": "db18",
    "port": 5432,
    "user": "u",
    "database": "d",
    "columns": ("time", "host"),
  }
  base.update(kw)
  return mod.CopyOptions(**base)


def _patch_side_effects(mod: object, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(mod, "fetch_chunk_signatures", lambda *_a, **_k: {})
  monkeypatch.setattr(mod, "decompress_target_range", lambda *_a, **_k: None)
  monkeypatch.setattr(mod, "compress_target_range", lambda *_a, **_k: 0)


def _sample_chunk(mod: object) -> object:
  return mod.ChunkRow(
    "_timescaledb_internal",
    "_hyper_1_2_chunk",
    datetime(2026, 8, 10, tzinfo=UTC),
    datetime(2026, 8, 11, tzinfo=UTC),
    False,
  )


def test_copy_one_chunk_skips_when_source_and_target_counts_match(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Already-synced ranges must not delete/COPY again."""
  mod = _load_mod()
  chunk = _sample_chunk(mod)
  executed: list[str] = []

  class _FakeCursor:
    def __enter__(self) -> _FakeCursor:
      return self

    def __exit__(self, *_a: object) -> None:
      return None

    def execute(self, sql: str, _params: object = None) -> None:
      executed.append(sql if isinstance(sql, str) else str(sql))

    def fetchone(self) -> tuple[int]:
      return (42,)

    def copy(self, _sql: str) -> object:
      raise AssertionError("COPY must not run when counts match")

  class _FakeConn:
    def __enter__(self) -> _FakeConn:
      return self

    def __exit__(self, *_a: object) -> None:
      return None

    def cursor(self) -> _FakeCursor:
      return _FakeCursor()

  _patch_side_effects(mod, monkeypatch)
  monkeypatch.setattr(mod, "connect_pg", lambda **_kw: _FakeConn())
  result = mod.copy_one_chunk(chunk, opts=_opts(mod, force=False)).outcome
  assert result == "skipped"
  assert not any("DELETE FROM host_data" in s for s in executed)


def test_copy_one_chunk_copies_when_counts_differ(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  mod = _load_mod()
  chunk = _sample_chunk(mod)
  executed: list[str] = []
  fetch_values = iter([100, 50, 100])  # source, target, post-copy target

  class _FakeCopy:
    def __enter__(self) -> _FakeCopy:
      return self

    def __exit__(self, *_a: object) -> None:
      return None

    def __iter__(self):
      return iter(())

    def write(self, _data: object) -> None:
      return None

  class _FakeCursor:
    def __enter__(self) -> _FakeCursor:
      return self

    def __exit__(self, *_a: object) -> None:
      return None

    def execute(self, sql: str, _params: object = None) -> None:
      executed.append(sql if isinstance(sql, str) else str(sql))

    def fetchone(self) -> tuple[int]:
      return (next(fetch_values),)

    def copy(self, _sql: str) -> _FakeCopy:
      return _FakeCopy()

  class _FakeConn:
    def __enter__(self) -> _FakeConn:
      return self

    def __exit__(self, *_a: object) -> None:
      return None

    def cursor(self) -> _FakeCursor:
      return _FakeCursor()

  _patch_side_effects(mod, monkeypatch)
  monkeypatch.setattr(mod, "connect_pg", lambda **_kw: _FakeConn())
  result = mod.copy_one_chunk(chunk, opts=_opts(mod, force=False)).outcome
  assert result == "copied"
  assert any("DELETE FROM host_data" in s for s in executed)


def test_copy_one_chunk_force_recopies_even_when_counts_match(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  mod = _load_mod()
  chunk = _sample_chunk(mod)
  executed: list[str] = []

  class _FakeCopy:
    def __enter__(self) -> _FakeCopy:
      return self

    def __exit__(self, *_a: object) -> None:
      return None

    def __iter__(self):
      return iter(())

    def write(self, _data: object) -> None:
      return None

  class _FakeCursor:
    def __enter__(self) -> _FakeCursor:
      return self

    def __exit__(self, *_a: object) -> None:
      return None

    def execute(self, sql: str, _params: object = None) -> None:
      executed.append(sql if isinstance(sql, str) else str(sql))

    def fetchone(self) -> tuple[int]:
      return (7,)

    def copy(self, _sql: str) -> _FakeCopy:
      return _FakeCopy()

  class _FakeConn:
    def __enter__(self) -> _FakeConn:
      return self

    def __exit__(self, *_a: object) -> None:
      return None

    def cursor(self) -> _FakeCursor:
      return _FakeCursor()

  _patch_side_effects(mod, monkeypatch)
  monkeypatch.setattr(mod, "connect_pg", lambda **_kw: _FakeConn())
  result = mod.copy_one_chunk(chunk, opts=_opts(mod, force=True)).outcome
  assert result == "copied"
  assert any("DELETE FROM host_data" in s for s in executed)


def test_run_chunk_copies_parallel_invokes_each_chunk(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """``--workers 2`` must still process every selected chunk."""
  mod = _load_mod()
  c1 = mod.ChunkRow(
    "_timescaledb_internal",
    "_hyper_1_1_chunk",
    datetime(2026, 8, 1, tzinfo=UTC),
    datetime(2026, 8, 2, tzinfo=UTC),
    False,
  )
  c2 = mod.ChunkRow(
    "_timescaledb_internal",
    "_hyper_1_2_chunk",
    datetime(2026, 8, 2, tzinfo=UTC),
    datetime(2026, 8, 3, tzinfo=UTC),
    False,
  )
  seen: list[str] = []

  def _fake_copy(chunk: object, **_kw: object) -> object:
    seen.append(chunk.chunk_name)  # type: ignore[attr-defined]
    outcome = "skipped" if chunk.chunk_name.endswith("1_chunk") else "copied"  # type: ignore[attr-defined]
    return mod.ChunkResult(outcome, 1, ())

  monkeypatch.setattr(mod, "copy_one_chunk", _fake_copy)
  summary = mod.run_chunk_copies([c1, c2], opts=_opts(mod), workers=2)
  assert summary.skipped == 1 and summary.copied == 1
  assert set(seen) == {"_hyper_1_1_chunk", "_hyper_1_2_chunk"}


def test_run_chunk_copies_rejects_non_positive_workers() -> None:
  mod = _load_mod()
  with pytest.raises(ValueError, match="workers"):
    mod.run_chunk_copies([], opts=_opts(mod), workers=0)


def test_count_target_range_rows_disables_statement_timeout_first() -> None:
  """Regression: large host_data range counts hit QueryCanceled without timeout=0."""
  mod = _load_mod()
  chunk = _sample_chunk(mod)
  executed: list[str] = []

  class _FakeCursor:
    def __enter__(self) -> _FakeCursor:
      return self

    def __exit__(self, *_a: object) -> None:
      return None

    def execute(self, sql: str, _params: object = None) -> None:
      executed.append(sql if isinstance(sql, str) else str(sql))

    def fetchone(self) -> tuple[int]:
      return (1,)

  class _FakeConn:
    def cursor(self) -> _FakeCursor:
      return _FakeCursor()

  mod.count_target_range_rows(_FakeConn(), chunk)
  assert executed, "expected SQL"
  assert any("statement_timeout" in s for s in executed[:2])
  assert any("max_parallel_workers_per_gather" in s for s in executed[:3])
  assert any("count(*)" in s.lower() for s in executed)


def test_count_source_chunk_rows_disables_statement_timeout_first() -> None:
  mod = _load_mod()
  chunk = _sample_chunk(mod)
  executed: list[str] = []

  class _FakeCursor:
    def __enter__(self) -> _FakeCursor:
      return self

    def __exit__(self, *_a: object) -> None:
      return None

    def execute(self, sql: str, _params: object = None) -> None:
      executed.append(sql if isinstance(sql, str) else str(sql))

    def fetchone(self) -> tuple[int]:
      return (1,)

  class _FakeConn:
    def cursor(self) -> _FakeCursor:
      return _FakeCursor()

  mod.count_source_chunk_rows(_FakeConn(), chunk)
  assert any("statement_timeout" in s for s in executed[:2])
  assert any("max_parallel_workers_per_gather" in s for s in executed[:3])
  assert any("count(*)" in s.lower() for s in executed)


def test_count_target_retries_operation_canceled_then_succeeds(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Regression: PG18 io_uring 'Operation canceled' on skip-check must retry."""
  from psycopg import errors as pg_errors

  mod = _load_mod()
  chunk = _sample_chunk(mod)
  monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
  calls = {"n": 0}

  class _FakeCursor:
    def __enter__(self) -> _FakeCursor:
      return self

    def __exit__(self, *_a: object) -> None:
      return None

    def execute(self, sql: str, _params: object = None) -> None:
      if "count(*)" in sql.lower():
        calls["n"] += 1
        if calls["n"] < 3:
          raise pg_errors.InternalError_(
            "could not read blocks 143..158 in file "
            '"base/16388/39111": Operation canceled'
          )

    def fetchone(self) -> tuple[int]:
      return (42,)

  class _FakeConn:
    def cursor(self) -> _FakeCursor:
      return _FakeCursor()

  assert mod.count_target_range_rows(_FakeConn(), chunk) == 42
  assert calls["n"] == 3


def test_count_target_gives_up_as_minus_one(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Persistent Operation canceled returns -1 so copy falls through (no abort)."""
  from psycopg import errors as pg_errors

  mod = _load_mod()
  chunk = _sample_chunk(mod)
  monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

  class _FakeCursor:
    def __enter__(self) -> _FakeCursor:
      return self

    def __exit__(self, *_a: object) -> None:
      return None

    def execute(self, sql: str, _params: object = None) -> None:
      if "count(*)" in sql.lower():
        raise pg_errors.InternalError_(
          "could not read blocks 143..158: Operation canceled"
        )

    def fetchone(self) -> tuple[int]:
      return (0,)

  class _FakeConn:
    def cursor(self) -> _FakeCursor:
      return _FakeCursor()

  assert mod.count_target_range_rows(_FakeConn(), chunk) == -1


def test_copy_one_chunk_recopies_when_counts_unknown(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Both counts -1 must not skip; delete+COPY must still run."""
  mod = _load_mod()
  chunk = _sample_chunk(mod)
  executed: list[str] = []

  class _FakeCopy:
    def __enter__(self) -> _FakeCopy:
      return self

    def __exit__(self, *_a: object) -> None:
      return None

    def __iter__(self):
      return iter(())

    def write(self, _data: object) -> None:
      return None

  class _FakeCursor:
    def __enter__(self) -> _FakeCursor:
      return self

    def __exit__(self, *_a: object) -> None:
      return None

    def execute(self, sql: str, _params: object = None) -> None:
      executed.append(sql if isinstance(sql, str) else str(sql))

    def copy(self, _sql: str) -> _FakeCopy:
      return _FakeCopy()

  class _FakeConn:
    def __enter__(self) -> _FakeConn:
      return self

    def __exit__(self, *_a: object) -> None:
      return None

    def cursor(self) -> _FakeCursor:
      return _FakeCursor()

  _patch_side_effects(mod, monkeypatch)
  monkeypatch.setattr(mod, "connect_pg", lambda **_kw: _FakeConn())
  monkeypatch.setattr(mod, "count_source_chunk_rows", lambda *_a, **_k: -1)
  # Pre-copy target count unknown; post-copy recount succeeds.
  tgt_counts = iter([-1, 5])
  monkeypatch.setattr(
    mod, "count_target_range_rows", lambda *_a, **_k: next(tgt_counts)
  )

  outcome = mod.copy_one_chunk(chunk, opts=_opts(mod, force=False)).outcome
  assert outcome == "copied"
  assert any("DELETE FROM host_data" in s for s in executed)


def test_chunk_dump_uses_fast_zstd_with_four_threads() -> None:
  """Dump uses -T4 (never all cores) and a fast level: -19 throttled COPY."""
  mod = _load_mod()
  assert mod._ZSTD_DUMP_ARGS == ("zstd", "-T4", "-3")


def _chunk(mod: object, day: int, name: str = "_hyper_1_2_chunk") -> object:
  return mod.ChunkRow(
    "_timescaledb_internal",
    name,
    datetime(2026, 8, day, tzinfo=UTC),
    datetime(2026, 8, day + 1, tzinfo=UTC),
    False,
  )


def test_matched_copy_columns_uses_target_order() -> None:
  mod = _load_mod()
  cols = mod.matched_copy_columns(
    [("host", "character varying(64)"), ("time", "timestamptz")],
    [("time", "timestamptz"), ("host", "character varying(64)")],
  )
  assert cols == ("time", "host")


@pytest.mark.parametrize(
  "source",
  [
    [("time", "timestamptz")],  # target has a column the source lacks
    [
      ("time", "timestamptz"),
      ("host", "text"),
    ],  # retyped -> binary COPY breaks
    [("time", "timestamptz"), ("host", "character varying(64)"), ("x", "int")],
  ],
)
def test_matched_copy_columns_fails_closed_on_layout_drift(
  source: list[tuple[str, str]],
) -> None:
  """Binary COPY must never run across differing column sets or types."""
  mod = _load_mod()
  with pytest.raises(ValueError, match="layout differs"):
    mod.matched_copy_columns(
      source, [("time", "timestamptz"), ("host", "character varying(64)")]
    )


def test_chunk_signature_sql_is_psycopg_placeholder_safe() -> None:
  """Regression: format('%I') in a parameterized query breaks psycopg."""
  mod = _load_mod()
  one = mod.chunk_signature_sql(one_chunk=True)
  assert one.count("%") == one.count("%s") == 2
  assert "%" not in mod.chunk_signature_sql(one_chunk=False)
  assert "compressed_chunk_id" in one
  # Hub 2.28 catalog has no chunk.dropped (smoke test 2026-11-06 failed on it).
  assert "dropped" not in one


def test_split_unchanged_only_trusts_identical_sig_and_bounds() -> None:
  mod = _load_mod()
  same, sig_moved, bounds_moved, missing = (
    _chunk(mod, 1, "a"),
    _chunk(mod, 2, "b"),
    _chunk(mod, 3, "c"),
    _chunk(mod, 4, "d"),
  )
  sig = (10, 0, 0, 0, 0, 0)
  state = {
    c.regclass: mod.state_entry(c, mod.ChunkResult("copied", 10, sig))
    for c in (same, sig_moved, bounds_moved)
  }
  state[bounds_moved.regclass]["range_end"] = "2099-01-01T00:00:00+00:00"
  sigs = {
    same.regclass: sig,
    sig_moved.regclass: (11, 0, 0, 0, 0, 0),
    bounds_moved.regclass: sig,
    missing.regclass: sig,
  }
  examine, unchanged = mod.split_unchanged(
    [same, sig_moved, bounds_moved, missing], state, sigs
  )
  assert unchanged == [same]
  assert examine == [sig_moved, bounds_moved, missing]


def test_unchanged_requires_recorded_non_negative_rows_and_live_sig() -> None:
  mod = _load_mod()
  c = _chunk(mod, 1)
  entry = mod.state_entry(c, mod.ChunkResult("copied", -1, (1,)))
  assert mod.chunk_unchanged_since_state(c, entry, (1,)) is False
  good = mod.state_entry(c, mod.ChunkResult("copied", 1, (1,)))
  assert mod.chunk_unchanged_since_state(c, good, None) is False
  assert mod.chunk_unchanged_since_state(c, None, (1,)) is False


def test_state_round_trip_and_version_gate(tmp_path: Path) -> None:
  mod = _load_mod()
  path = tmp_path / "sub" / "state.json"
  rec = {"x": {"rows": 1, "sig": [1]}}
  mod.save_state(str(path), rec)
  assert mod.load_state(str(path)) == rec
  assert not (tmp_path / "sub" / "state.json.tmp").exists()
  path.write_text('{"version": 999, "chunks": {}}')
  with pytest.raises(ValueError, match="version"):
    mod.load_state(str(path))
  assert mod.load_state(str(tmp_path / "missing.json")) == {}


class _NullCopy:
  def __enter__(self) -> _NullCopy:
    return self

  def __exit__(self, *_a: object) -> None:
    return None

  def __iter__(self):
    return iter(())

  def write(self, _data: object) -> None:
    return None


class _LogCursor:
  def __init__(self, log: list[str]) -> None:
    self.log = log

  def __enter__(self) -> _LogCursor:
    return self

  def __exit__(self, *_a: object) -> None:
    return None

  def execute(self, sql: str, _params: object = None) -> None:
    self.log.append(sql)

  def copy(self, sql: str) -> _NullCopy:
    self.log.append(sql)
    return _NullCopy()


class _LogConn:
  def __init__(self, log: list[str]) -> None:
    self.log = log

  def __enter__(self) -> _LogConn:
    return self

  def __exit__(self, *_a: object) -> None:
    return None

  def cursor(self) -> _LogCursor:
    return _LogCursor(self.log)


def test_copy_one_chunk_orders_sig_decompress_delete_copy_compress(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Signature first (so later writes show), decompress before DELETE."""
  mod = _load_mod()
  log: list[str] = []
  monkeypatch.setattr(mod, "connect_pg", lambda **_kw: _LogConn(log))
  monkeypatch.setattr(
    mod,
    "fetch_chunk_signatures",
    lambda _c, chunk=None: log.append("SIG") or {chunk.regclass: (9,)},
  )
  counts = iter([10, 0, 10])  # source, target, post-copy target
  monkeypatch.setattr(
    mod,
    "count_source_chunk_rows",
    lambda *_a: log.append("COUNT_SRC") or 10,
  )
  monkeypatch.setattr(
    mod,
    "count_target_range_rows",
    lambda *_a: log.append("COUNT_TGT") or next(counts),
  )
  monkeypatch.setattr(
    mod, "decompress_target_range", lambda *_a: log.append("DECOMPRESS")
  )
  monkeypatch.setattr(
    mod, "compress_target_range", lambda *_a: log.append("COMPRESS") or 1
  )
  next(counts)  # source count is patched separately; drop its slot
  res = mod.copy_one_chunk(_chunk(mod, 1), opts=_opts(mod))
  assert res.outcome == "copied" and res.rows == 10 and res.sig == (9,)
  order = [
    x if x.isupper() and " " not in x else x.split()[0]
    for x in log
    if x in {"SIG", "COUNT_SRC", "COUNT_TGT", "DECOMPRESS", "COMPRESS"}
    or x.startswith(("DELETE", "COPY"))
  ]
  assert order == [
    "SIG",
    "COUNT_SRC",
    "COUNT_TGT",
    "DECOMPRESS",
    "DELETE",
    "COPY",
    "COPY",
    "COUNT_TGT",
    "COMPRESS",
  ]
  copies = [x for x in log if x.startswith("COPY")]
  assert all("FORMAT binary" in x for x in copies)


def test_copy_one_chunk_verify_raises_on_post_copy_mismatch(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Freeze --verify: a source/target difference after COPY must fail."""
  mod = _load_mod()
  log: list[str] = []
  _patch_side_effects(mod, monkeypatch)
  monkeypatch.setattr(mod, "connect_pg", lambda **_kw: _LogConn(log))
  src_counts = iter([10, 11])
  monkeypatch.setattr(
    mod, "count_source_chunk_rows", lambda *_a: next(src_counts)
  )
  tgt_counts = iter([0, 10])
  monkeypatch.setattr(
    mod, "count_target_range_rows", lambda *_a: next(tgt_counts)
  )
  with pytest.raises(RuntimeError, match="verify mismatch"):
    mod.copy_one_chunk(_chunk(mod, 1), opts=_opts(mod, verify=True))


def test_copy_one_chunk_raises_when_post_copy_count_unknown(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """A copied chunk with an unknown target count must not be recorded."""
  mod = _load_mod()
  _patch_side_effects(mod, monkeypatch)
  monkeypatch.setattr(mod, "connect_pg", lambda **_kw: _LogConn([]))
  monkeypatch.setattr(mod, "count_source_chunk_rows", lambda *_a: 10)
  monkeypatch.setattr(mod, "count_target_range_rows", lambda *_a: -1)
  with pytest.raises(RuntimeError, match="post-copy target count"):
    mod.copy_one_chunk(_chunk(mod, 1), opts=_opts(mod))


def test_compress_target_range_only_old_inside_uncompressed(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  mod = _load_mod()
  chunk = mod.ChunkRow(
    "_timescaledb_internal",
    "_hyper_1_7_chunk",
    datetime(2026, 8, 1, tzinfo=UTC),
    datetime(2026, 8, 8, tzinfo=UTC),
    True,
  )
  d = lambda day: datetime(2026, 8, day, tzinfo=UTC)  # noqa: E731
  overlap = [
    ("t.inside_old", False, d(1), d(2)),
    ("t.already", True, d(2), d(3)),
    ("t.recent_inside", False, d(7), d(8)),
    ("t.straddles", False, d(7), d(9)),
  ]
  monkeypatch.setattr(mod, "_target_chunks_overlapping", lambda *_a: overlap)
  log: list[object] = []

  class _Cur(_LogCursor):
    def execute(self, sql: str, params: object = None) -> None:
      self.log.append(params or sql)

  class _Conn:
    def cursor(self) -> _Cur:
      return _Cur(log)

  assert mod.compress_target_range(_Conn(), chunk, d(7)) == 0  # chunk too new
  assert mod.compress_target_range(_Conn(), chunk, None) == 0
  assert log == []  # early return: no compress query for a too-new chunk
  assert mod.compress_target_range(_Conn(), chunk, d(8)) == 2
  assert ("t.inside_old",) in log and ("t.recent_inside",) in log
  assert not any(x in log for x in [("t.already",), ("t.straddles",)])


def test_run_chunk_copies_continues_after_failure_and_saves_state(
  monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
  """One bad chunk must not abort a multi-day run or keep a stale record."""
  mod = _load_mod()
  good, bad = _chunk(mod, 1, "good"), _chunk(mod, 2, "bad")

  def _fake(chunk: object, **_kw: object) -> object:
    if chunk.chunk_name == "bad":  # type: ignore[attr-defined]
      raise RuntimeError("boom")
    return mod.ChunkResult("copied", 7, (7,))

  monkeypatch.setattr(mod, "copy_one_chunk", _fake)
  path = tmp_path / "state.json"
  state = {bad.regclass: {"rows": 1, "sig": [1]}}
  summary = mod.run_chunk_copies(
    [good, bad], opts=_opts(mod), workers=2, state=state, state_path=str(path)
  )
  assert (summary.copied, summary.failed, summary.rows_total) == (1, 1, 7)
  saved = mod.load_state(str(path))
  assert saved[good.regclass]["rows"] == 7
  assert bad.regclass not in saved


def test_main_changed_only_requires_state_file() -> None:
  mod = _load_mod()
  assert mod.main(["--changed-only"]) == 2


def _prior(mod: object, chunk: object, sig: tuple[int, ...]) -> dict:
  return mod.state_entry(chunk, mod.ChunkResult("copied", 10, sig))


@pytest.mark.parametrize(
  ("now_sig", "ok"),
  [
    ((15, 0, 0, 0, 0, 0), True),  # inserts only
    ((15, 1, 0, 0, 0, 0), False),  # heap UPDATE (repair tool)
    ((15, 0, 3, 0, 0, 0), False),  # heap DELETE / compression move
    ((10, 0, 0, 5, 0, 1), False),  # compressed batch decompressed
    ((15, 0, 0), False),  # malformed signature
  ],
)
def test_inserts_only_since_gates_slice_resync(
  now_sig: tuple[int, ...], ok: bool
) -> None:
  mod = _load_mod()
  c = _chunk(mod, 1)
  assert (
    mod.inserts_only_since(c, _prior(mod, c, (10, 0, 0, 0, 0, 0)), now_sig)
    is ok
  )
  assert mod.inserts_only_since(c, None, now_sig) is False


def test_mismatched_slices_returns_only_differing_clamped_windows() -> None:
  mod = _load_mod()
  c = _chunk(mod, 1)
  h = lambda hour: datetime(2026, 8, 1, hour, tzinfo=UTC)  # noqa: E731
  src = {h(0): 5, h(1): 7, h(23): 2}
  tgt = {h(0): 5, h(1): 6}
  assert mod.mismatched_slices(c, src, tgt, 60) == [
    (h(1), h(2)),
    (h(23), datetime(2026, 8, 2, tzinfo=UTC)),
  ]


def test_copy_one_chunk_slice_resync_copies_only_mismatched_slices(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Freeze hot chunk: delete + COPY only the slices that gained rows."""
  mod = _load_mod()
  c = _chunk(mod, 1)
  log: list[str] = []
  monkeypatch.setattr(mod, "connect_pg", lambda **_kw: _LogConn(log))
  monkeypatch.setattr(
    mod, "fetch_chunk_signatures", lambda *_a: {c.regclass: (15, 0, 0, 0, 0, 0)}
  )
  monkeypatch.setattr(mod, "decompress_target_range", lambda *_a: None)
  monkeypatch.setattr(mod, "compress_target_range", lambda *_a: 0)
  monkeypatch.setattr(mod, "count_source_chunk_rows", lambda *_a: 15)
  tgt = iter([10, 15])
  monkeypatch.setattr(mod, "count_target_range_rows", lambda *_a: next(tgt))
  h = lambda hour: datetime(2026, 8, 1, hour, tzinfo=UTC)  # noqa: E731
  monkeypatch.setattr(
    mod,
    "fetch_slice_counts",
    lambda _c, _ch, _m, *, source: (
      {h(3): 9, h(4): 6} if source else {h(3): 4, h(4): 6}
    ),
  )
  res = mod.copy_one_chunk(
    c, opts=_opts(mod, verify=True), prior=_prior(mod, c, (10, 0, 0, 0, 0, 0))
  )
  assert res.rows == 15
  deletes = [x for x in log if x.startswith("DELETE")]
  outs = [x for x in log if x.startswith("COPY (SELECT")]
  assert len(deletes) == 1 and "2026-08-01T03:00:00" in deletes[0]
  assert "2026-08-01T04:00:00" in deletes[0]
  assert len(outs) == 1 and " WHERE time >= TIMESTAMPTZ" in outs[0]


def test_copy_one_chunk_full_copy_when_rows_were_updated(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """An UPDATE since the last pass keeps counts equal per slice: copy whole."""
  mod = _load_mod()
  c = _chunk(mod, 1)
  log: list[str] = []
  monkeypatch.setattr(mod, "connect_pg", lambda **_kw: _LogConn(log))
  monkeypatch.setattr(
    mod, "fetch_chunk_signatures", lambda *_a: {c.regclass: (10, 4, 0, 0, 0, 0)}
  )
  monkeypatch.setattr(mod, "decompress_target_range", lambda *_a: None)
  monkeypatch.setattr(mod, "compress_target_range", lambda *_a: 0)
  monkeypatch.setattr(
    mod,
    "fetch_slice_counts",
    lambda *_a, **_k: pytest.fail("slice counts must not run"),
  )
  monkeypatch.setattr(mod, "count_source_chunk_rows", lambda *_a: 10)
  tgt = iter([9, 10])
  monkeypatch.setattr(mod, "count_target_range_rows", lambda *_a: next(tgt))
  mod.copy_one_chunk(
    c, opts=_opts(mod), prior=_prior(mod, c, (10, 0, 0, 0, 0, 0))
  )
  outs = [x for x in log if x.startswith("COPY (SELECT")]
  assert len(outs) == 1 and " WHERE " not in outs[0]


def test_copy_one_chunk_full_copy_when_slice_count_canceled(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  mod = _load_mod()
  c = _chunk(mod, 1)
  log: list[str] = []
  monkeypatch.setattr(mod, "connect_pg", lambda **_kw: _LogConn(log))
  monkeypatch.setattr(
    mod, "fetch_chunk_signatures", lambda *_a: {c.regclass: (15, 0, 0, 0, 0, 0)}
  )
  monkeypatch.setattr(mod, "decompress_target_range", lambda *_a: None)
  monkeypatch.setattr(mod, "compress_target_range", lambda *_a: 0)
  monkeypatch.setattr(mod, "fetch_slice_counts", lambda *_a, **_k: None)
  monkeypatch.setattr(mod, "count_source_chunk_rows", lambda *_a: 15)
  tgt = iter([10, 15])
  monkeypatch.setattr(mod, "count_target_range_rows", lambda *_a: next(tgt))
  mod.copy_one_chunk(
    c, opts=_opts(mod), prior=_prior(mod, c, (10, 0, 0, 0, 0, 0))
  )
  outs = [x for x in log if x.startswith("COPY (SELECT")]
  assert len(outs) == 1 and " WHERE " not in outs[0]
