"""INI-gated parse-stage telemetry (Wave 3 Approach D)."""
from __future__ import annotations

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    IncrementalStatsParser,
    build_stats_dataframes,
    compute_deltas_and_arc,
    reset_parse_stage_timing,
    snapshot_parse_stage_timing,
)


_MINIMAL_LINES = [
    "1709123456 job1 cn001\n",
    "!cpu user,W=48 sys,W=48\n",
    "cpu 0 100 200\n",
]


def test_parse_stage_telemetry_off_snapshot_empty():
  """Default-off telemetry must not emit stage keys."""
  reset_parse_stage_timing(enabled=False)
  parser = IncrementalStatsParser(0)
  parser.feed_lines(_MINIMAL_LINES)
  cols = parser.take_stats_columns()
  stats_df, _ = build_stats_dataframes(cols, [])
  if not stats_df.empty:
    compute_deltas_and_arc(stats_df)
  assert snapshot_parse_stage_timing() == {}
  meta = st._merge_ingest_write_timing_into_meta({})
  assert "feed_s" not in meta
  assert "collapse_s" not in meta
  assert "build_df_s" not in meta


def test_parse_stage_telemetry_on_accumulates_non_negative():
  """When enabled, stage keys are present, non-negative, and sum-bounded."""
  reset_parse_stage_timing(enabled=True)
  try:
    parser = IncrementalStatsParser(0)
    parser.feed_lines(_MINIMAL_LINES)
    cols = parser.take_stats_columns()
    stats_df, _ = build_stats_dataframes(cols, [])
    if not stats_df.empty:
      compute_deltas_and_arc(stats_df)
    snap = snapshot_parse_stage_timing()
    assert set(snap) == {"feed_s", "collapse_s", "build_df_s"}
    assert all(v >= 0.0 for v in snap.values())
    meta = st._merge_ingest_write_timing_into_meta({"parse_elapsed_s": 1.0})
    assert meta["feed_s"] == snap["feed_s"]
    stage_sum = meta["feed_s"] + meta["collapse_s"] + meta["build_df_s"]
    assert stage_sum <= float(meta["parse_elapsed_s"]) + 0.5
  finally:
    reset_parse_stage_timing(enabled=False)


def test_parse_stage_telemetry_outcome_log_tokens(monkeypatch):
  """Outcome log emits stage tokens only when present on the outcome."""
  monkeypatch.setattr(st, "stats_file_size_bytes", lambda _p: 100)
  logged: list[str] = []

  def _capture(*args, **kwargs):
    del kwargs
    logged.append(" ".join(str(a) for a in args))

  old = st.log_print
  st.log_print = _capture
  try:
    st._log_ingest_file_outcome(
        st.IngestFileOutcome(
            path="/x",
            elapsed_s=10.0,
            ingest_ok=True,
            need_archival=False,
            outcome="ingested",
            parse_elapsed_s=8.0,
            postgres_s=1.0,
            feed_s=5.0,
            collapse_s=2.0,
            build_df_s=0.5,
        ),
    )
    st._log_ingest_file_outcome(
        st.IngestFileOutcome(
            path="/y",
            elapsed_s=1.0,
            ingest_ok=True,
            need_archival=False,
            outcome="ingested",
            parse_elapsed_s=0.5,
        ),
    )
  finally:
    st.log_print = old
  joined = " ".join(logged)
  assert "feed_s=5.0" in joined
  assert "collapse_s=2.0" in joined
  assert "build_df_s=0.5" in joined
  assert joined.count("feed_s=") == 1
