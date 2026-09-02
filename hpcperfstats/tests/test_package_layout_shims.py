"""Regression: library modules resolve under colocated lib/ trees."""
from __future__ import annotations

import importlib


def test_dbload_lib_conf_parser_importable():
  mod = importlib.import_module("hpcperfstats.dbload.lib.conf_parser")
  assert hasattr(mod, "get_debug")


def test_dbload_lib_sync_timedb_parsing_importable():
  mod = importlib.import_module("hpcperfstats.dbload.lib.sync_timedb_parsing")
  assert hasattr(mod, "parse_first_timestamp_line")


def test_dbload_lib_sync_timedb_append_day_lists_importable():
  mod = importlib.import_module(
      "hpcperfstats.dbload.lib.sync_timedb_append_day_lists",
  )
  assert hasattr(mod, "AppendDayClaimLists")


def test_analysis_metrics_lib_metrics_importable():
  mod = importlib.import_module("hpcperfstats.analysis.metrics.lib.metrics")
  assert mod is not None


def test_site_lib_machine_models_importable():
  mod = importlib.import_module("hpcperfstats.site.lib.machine.models")
  assert hasattr(mod, "host_data")
