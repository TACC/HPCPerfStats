"""Legacy stats-file CTL/CTR decode (sync_timedb_parsing_legacy)."""
from hpcperfstats.dbload.lib.sync_timedb_parsing_legacy import (
    EVENTMAPS_BY_TYPE,
    map_hardware_counter_vals,
)


def test_map_hardware_counter_vals_fixed_ctr():
  schema_events = ["FIXED_CTR0,W=48", "FIXED_CTR1,W=48"]
  eventmap = {"FIXED_CTR0": "INST_RETIRED,W=48", "FIXED_CTR1": "APERF,W=48"}
  vals = [100, 200]
  result = map_hardware_counter_vals("intel_8pmc3", schema_events, vals, eventmap)
  assert result["INST_RETIRED,W=48"] == 100
  assert result["APERF,W=48"] == 200


def test_map_hardware_counter_vals_ctl_ctr():
  schema_events = ["CTL0", "CTR0"]
  eventmap = {0: "EVENT_A,W=48", 1: "EVENT_B,W=48"}
  vals = [0, 100]
  result = map_hardware_counter_vals("amd64_pmc", schema_events, vals, eventmap)
  assert result["EVENT_A,W=48"] == 100


def test_map_hardware_counter_vals_plain():
  schema_events = ["EV1,W=48", "EV2,W=48"]
  eventmap = {}
  vals = [10, 20]
  result = map_hardware_counter_vals("other", schema_events, vals, eventmap)
  assert result["EV1"] == 10
  assert result["EV2"] == 20


def test_eventmaps_include_knl_legacy_types():
  assert "intel_knl_mc_dclk" in EVENTMAPS_BY_TYPE
  assert "intel_knl_mc" in EVENTMAPS_BY_TYPE
