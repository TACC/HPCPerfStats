"""Drift guard: the update_metrics state doc covers the backfill lane."""

from __future__ import annotations

import os

import pytest

ROOT = os.path.dirname(
  os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
DOC = os.path.join(ROOT, "docs", "reference", "update-metrics-state-machine.md")


@pytest.fixture(scope="module")
def doc():
  with open(DOC, encoding="utf-8") as fh:
    return fh.read()


def test_state_doc_has_backfill_lane_section(doc):
  assert "### Section 4.5 Backfill lane" in doc


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
    "backfill=v:,slots:k/n,",
    "backfill_db(hourly)=dirty:,stale:,",
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
    # mdformat pads table cells, so compare stripped cells, not raw text.
    cells = next(
      cells
      for line in doc.splitlines()
      if (cells := [c.strip() for c in line.strip().strip("|").split("|")])
      and cells[0] == f"`{key}`"
    )
    assert cells[1] == default, (key, cells)
