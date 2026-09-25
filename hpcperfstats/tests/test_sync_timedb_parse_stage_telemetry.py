"""INI-gated exhaustive parse-stage telemetry (telem v2)."""
from __future__ import annotations

from pathlib import Path

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    PARSE_STAGE_BUILD_DF_PARTS,
    PARSE_STAGE_HOLD_KEYS,
    PARSE_STAGE_LOG_KEYS,
    IncrementalStatsParser,
    attach_parse_unaccounted,
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
  assert "parse_unaccounted_s" not in meta


def test_parse_stage_telemetry_on_emits_all_hold_and_derived_keys():
  """When enabled, every hold key plus derived build_df/stages_sum is present."""
  reset_parse_stage_timing(enabled=True)
  try:
    parser = IncrementalStatsParser(0)
    parser.feed_lines(_MINIMAL_LINES)
    cols = parser.take_stats_columns()
    stats_df, _ = build_stats_dataframes(cols, [])
    if not stats_df.empty:
      compute_deltas_and_arc(stats_df)
    snap = snapshot_parse_stage_timing()
    for key in PARSE_STAGE_HOLD_KEYS:
      assert key in snap
      assert snap[key] >= 0.0
    assert "build_df_s" in snap and "stages_sum_s" in snap
    assert abs(
        snap["build_df_s"]
        - sum(snap[k] for k in PARSE_STAGE_BUILD_DF_PARTS)
    ) < 1e-9
    assert abs(
        snap["stages_sum_s"]
        - sum(snap[k] for k in PARSE_STAGE_HOLD_KEYS)
    ) < 1e-9
    meta = st._merge_ingest_write_timing_into_meta(
        {"parse_elapsed_s": max(1.0, snap["stages_sum_s"] + 0.5)},
    )
    assert "parse_unaccounted_s" in meta
    assert meta["parse_unaccounted_s"] >= 0.0
  finally:
    reset_parse_stage_timing(enabled=False)


def test_attach_parse_unaccounted_subtracts_postgres():
  """Closed-book residual subtracts nested postgres_s from parse wall."""
  out = attach_parse_unaccounted(
      {
          "parse_elapsed_s": 100.0,
          "stages_sum_s": 30.0,
          "postgres_s": 40.0,
      },
  )
  assert out["parse_unaccounted_s"] == 30.0


def test_parse_stage_telemetry_outcome_log_tokens(monkeypatch):
  """Outcome log emits exhaustive stage tokens from parse_stage dict."""
  monkeypatch.setattr(st, "stats_file_size_bytes", lambda _p: 100)
  logged: list[str] = []

  def _capture(*args, **kwargs):
    del kwargs
    logged.append(" ".join(str(a) for a in args))

  old = st.log_print
  st.log_print = _capture
  try:
    stage = {key: 0.0 for key in PARSE_STAGE_LOG_KEYS}
    stage.update(
        {
            "feed_s": 5.0,
            "collapse_s": 2.0,
            "build_df_s": 0.5,
            "proc_merge_s": 0.2,
            "hw_df_s": 0.2,
            "proc_df_s": 0.1,
            "stages_sum_s": 8.0,
            "parse_unaccounted_s": 1.0,
            "postgres_s": 1.0,
        },
    )
    # postgres is top-level on outcome; strip from stage for this unit
    stage.pop("postgres_s", None)
    st._log_ingest_file_outcome(
        st.IngestFileOutcome(
            path="/x",
            elapsed_s=10.0,
            ingest_ok=True,
            need_archival=False,
            outcome="ingested",
            parse_elapsed_s=8.0,
            postgres_s=1.0,
            parse_stage=stage,
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
  assert "proc_merge_s=0.2" in joined
  assert "parse_unaccounted_s=1.0" in joined
  assert joined.count("feed_s=") == 1


def test_parse_stage_telemetry_env_override_enables_without_ini(monkeypatch):
  """HPCPERFSTATS_SYNC_INGEST_PARSE_STAGE_TELEMETRY=1 enables without INI yes."""
  monkeypatch.setenv("HPCPERFSTATS_SYNC_INGEST_PARSE_STAGE_TELEMETRY", "1")
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_ingest_parse_stage_telemetry",
      lambda: False,
  )
  reset_parse_stage_timing(enabled=False)
  reset_parse_stage_timing(enabled=None)
  try:
    parser = IncrementalStatsParser(0)
    parser.feed_lines(_MINIMAL_LINES)
    snap = snapshot_parse_stage_timing()
    assert snap  # env forced on
    assert "feed_s" in snap
  finally:
    reset_parse_stage_timing(enabled=False)
    monkeypatch.delenv(
        "HPCPERFSTATS_SYNC_INGEST_PARSE_STAGE_TELEMETRY", raising=False,
    )


def test_build_stats_dataframes_has_no_outer_build_df_hold():
  """Outer build_df hold must be gone; split holds remain in source."""
  text = Path(__file__).resolve().parents[1].joinpath(
      "dbload/lib/sync_timedb_parsing.py",
  ).read_text(encoding="utf-8")
  body = text.split("def build_stats_dataframes")[1].split("\ndef ")[0]
  assert '_held_parse_stage("build_df")' not in body
  assert '_held_parse_stage("proc_merge_s")' in body
  assert '_held_parse_stage("hw_df_s")' in body
  assert '_held_parse_stage("proc_df_s")' in body
  assert '_held_parse_stage("lock_s")' in text
  assert '_held_parse_stage("decode_s")' in text
  assert '_held_parse_stage("delta_s")' in text
  assert '_held_parse_stage("arc_s")' in text
