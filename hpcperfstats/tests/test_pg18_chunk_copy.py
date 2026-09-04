"""Unit tests for scripts/pg18_host_data_chunk_copy.py helpers."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _load_mod():
    import sys

    path = Path(__file__).resolve().parents[2] / "scripts" / "pg18_host_data_chunk_copy.py"
    name = "pg18_host_data_chunk_copy"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_filter_chunks_by_watermark_excludes_hot_window() -> None:
    mod = _load_mod()
    wm = datetime(2026, 9, 1, tzinfo=timezone.utc)
    cold = mod.ChunkRow(
        "_timescaledb_internal",
        "_hyper_1_1_chunk",
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 2, tzinfo=timezone.utc),
        False,
    )
    hot = mod.ChunkRow(
        "_timescaledb_internal",
        "_hyper_1_99_chunk",
        datetime(2026, 8, 30, tzinfo=timezone.utc),
        datetime(2026, 9, 2, tzinfo=timezone.utc),
        True,
    )
    selected = mod.filter_chunks_by_watermark([cold, hot], watermark=wm)
    assert selected == [cold]


def test_build_copy_out_refuses_parent_hypertable() -> None:
    mod = _load_mod()
    parent = mod.ChunkRow(
        "public",
        "host_data",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 2, tzinfo=timezone.utc),
        False,
    )
    with pytest.raises(ValueError, match="parent hypertable"):
        mod.build_copy_out_sql(parent)


def test_build_delete_range_and_copy_sql_for_retry() -> None:
    mod = _load_mod()
    chunk = mod.ChunkRow(
        "_timescaledb_internal",
        "_hyper_1_2_chunk",
        datetime(2026, 8, 10, tzinfo=timezone.utc),
        datetime(2026, 8, 11, tzinfo=timezone.utc),
        False,
    )
    delete_sql = mod.build_delete_range_sql(chunk)
    assert "DELETE FROM host_data" in delete_sql
    assert "time >=" in delete_sql and "time <" in delete_sql
    assert "2026-08-10" in delete_sql and "2026-08-11" in delete_sql
    out_sql = mod.build_copy_out_sql(chunk)
    assert "COPY (SELECT * FROM _timescaledb_internal._hyper_1_2_chunk)" in out_sql
    assert "host_data" not in out_sql.split("FROM", 1)[1]
    assert mod.build_copy_in_sql() == "COPY host_data FROM STDIN"


def test_parse_chunk_tsv_and_watermark_days() -> None:
    mod = _load_mod()
    lines = [
        "_timescaledb_internal|_hyper_1_3_chunk|2026-07-01 00:00:00+00|2026-07-02 00:00:00+00|t",
    ]
    rows = mod.parse_chunk_tsv(lines)
    assert len(rows) == 1
    assert rows[0].is_compressed is True
    assert rows[0].range_end.day == 2
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    wm = mod.watermark_from_now(days=3, now=now)
    assert wm == datetime(2026, 9, 1, tzinfo=timezone.utc)


def test_fetch_source_chunks_uses_psycopg_not_psql_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: web image has no ``psql``; catalog fetch must use psycopg."""
    mod = _load_mod()

    def _forbid_subprocess(*_a: object, **_k: object) -> None:
        raise AssertionError("subprocess must not invoke psql for catalog fetch")

    monkeypatch.setattr(mod.subprocess, "run", _forbid_subprocess)
    monkeypatch.setattr(mod.subprocess, "Popen", _forbid_subprocess)

    class _FakeCursor:
        def __enter__(self) -> "_FakeCursor":
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
                    datetime(2026, 8, 1, tzinfo=timezone.utc),
                    datetime(2026, 8, 2, tzinfo=timezone.utc),
                    False,
                )
            ]

    class _FakeConn:
        def __enter__(self) -> "_FakeConn":
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
