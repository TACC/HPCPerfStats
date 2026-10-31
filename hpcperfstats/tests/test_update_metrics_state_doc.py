"""Drift guard: docs/UPDATE_METRICS_STATE_MACHINE.md covers the backfill lane."""

from __future__ import annotations

import os

import pytest

ROOT = os.path.dirname(
  os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
DOC = os.path.join(ROOT, "docs", "UPDATE_METRICS_STATE_MACHINE.md")


@pytest.fixture(scope="module")
def doc():
  with open(DOC, encoding="utf-8") as fh:
    return fh.read()


def test_state_doc_has_backfill_lane_section(doc):
  assert "### §4.5 Backfill lane" in doc


def test_state_doc_lists_backfill_states_and_storage(doc):
  for token in (
    "`awaiting_ingest`",
    "`partial_final`",
    "`metrics_version`",
    "`metrics_coverage`",
    "`metrics_skipped_since`",
    "`metrics_scheduler_state`",
    "METRICS_CONTRACT_VERSION",
  ):
    assert token in doc, token


def test_state_doc_census_and_knobs_cover_the_lane(doc):
  for token in (
    "backfill_remaining_stale=",
    "backfill_slots=",
    "`metrics_backfill_enabled`",
    "`metrics_backfill_min_slots`",
    "`metrics_backfill_fresh_reserve_slots`",
    "`metrics_backfill_quiescence_days`",
    "`metrics_backfill_max_jobs_per_hour`",
    "--backfill-status",
    "--backfill-finalize-partials",
  ):
    assert token in doc, token


def test_state_doc_knob_defaults_match_conf_parser(doc):
  from hpcperfstats.dbload.lib import conf_parser as cfg

  for key in (
    "metrics_backfill_enabled",
    "metrics_backfill_min_slots",
    "metrics_backfill_fresh_reserve_slots",
    "metrics_backfill_quiescence_days",
    "metrics_backfill_max_jobs_per_hour",
  ):
    default = cfg.INI_OPTION_DEFAULTS[key]
    row = next(
      line for line in doc.splitlines() if line.startswith(f"| `{key}` |")
    )
    assert f"| {default} |" in row, (key, row)
