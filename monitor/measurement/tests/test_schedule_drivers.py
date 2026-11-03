"""Block schedule + auto-fit, driver-group map (with source drift guard), journal parser."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from lib import driver_map, journal_types, schedule

FIX = Path(__file__).resolve().parents[1] / "fixtures"
MONITOR_SRC = Path(__file__).resolve().parents[2] / "src"


def _kinds(plan):
    return [(leg["kind"], leg.get("group")) for leg in plan["legs"]]


def test_schedule_blocks_and_autofit():
    groups = ["filesystem", "likwid", "ib_mad", "dcgm_cpu"]
    plan = schedule.build_schedule(groups, blocks=2, seed=42)
    kinds = _kinds(plan)
    assert kinds[0] == ("OFF", None) and kinds[1] == ("NONE", None) and kinds[2] == ("ALL", None)
    solo1 = [g for k, g in kinds[3:7]]
    assert sorted(solo1) == sorted(groups) and all(k == "SOLO" for k, _ in kinds[3:7])
    assert kinds[7] == ("ALL", None) and kinds[8] == ("OFF", None)
    assert kinds[9] == ("NONE", None)
    assert kinds[-1] == ("OFF", None)
    counts = {k: sum(1 for kk, _ in kinds if kk == k) for k in ("OFF", "NONE", "ALL", "SOLO")}
    assert counts == {"OFF": 3, "NONE": 2, "ALL": 4, "SOLO": 8}
    off_mid = plan["legs"][8]
    assert off_mid["brackets"] == [1, 2]
    assert plan["fit_steps"] == []
    hours = plan["projected_seconds"] / 3600
    assert 9.3 < hours - plan["setup_seconds"] / 3600 < 9.7
    assert plan["projected_seconds"] <= 12 * 3600
    again = schedule.build_schedule(groups, blocks=2, seed=42)
    assert _kinds(again) == kinds
    other = schedule.build_schedule(groups, blocks=2, seed=43)
    assert plan["seed"] == 42 and other["seed"] == 43
    ids = [leg["leg_id"] for leg in plan["legs"]]
    assert len(ids) == len(set(ids)), ids
    headline = [leg for leg in plan["legs"] if leg["fwq"]]
    assert {leg["kind"] for leg in headline} == {"OFF", "NONE", "ALL"}


def test_schedule_autofit_order_recorded():
    groups = [f"g{i}" for i in range(8)]
    plan = schedule.build_schedule(groups, blocks=2, seed=1)
    steps = [s["step"] for s in plan["fit_steps"]]
    assert steps[0] == "shorten_legs"
    assert plan["projected_seconds"] <= 12 * 3600
    order = ["shorten_legs", "drop_second_all", "solo_single_block"]
    assert steps == sorted(steps, key=order.index)
    assert plan["leg_seconds"] in (1200, 600)


def test_schedule_unfittable_reports_min_hours():
    with pytest.raises(schedule.ScheduleDoesNotFit) as err:
        schedule.build_schedule([f"g{i}" for i in range(80)], blocks=2, seed=1, max_hours=12)
    assert err.value.min_hours > 12
    assert "min" in str(err.value).lower()


def test_schedule_no_off_legs_and_optional_legs():
    plan = schedule.build_schedule(["filesystem"], blocks=1, seed=1, off_legs=False,
                                   cadence_sweep=[60, 300])
    kinds = [leg["kind"] for leg in plan["legs"]]
    assert "OFF" not in kinds
    sweep = [leg for leg in plan["legs"] if leg["kind"] == "CADENCE"]
    assert [leg["sample_freq"] for leg in sweep] == [60, 300]


def test_enabled_types_log_parse():
    text = (FIX / "journal_enabled_types.txt").read_text()
    assert journal_types.last_enabled_types(text) == ["host_block", "host_cpu", "host_mem"]
    assert journal_types.all_enabled_types(text)[0][:2] == ["host_block", "host_cpu"]
    assert journal_types.last_enabled_types((FIX / "journal_enabled_none.txt").read_text()) == []
    assert journal_types.last_enabled_types("Started hpcperfstatsd\n") is None
    prefixed = "Nov 02 10:00:00 c1 hpcperfstatsd[4242]: Enabled types: host_cpu,host_vm\n"
    assert journal_types.last_enabled_types(prefixed) == ["host_cpu", "host_vm"]


def _st_names() -> set[str]:
    macros: dict[str, str] = {}
    for header in MONITOR_SRC.glob("*.h"):
        for m in re.finditer(r'#define\s+(\w+_ST_NAME)\s+"([^"]+)"', header.read_text(errors="replace")):
            macros[m.group(1)] = m.group(2)
    names: set[str] = set()
    for src in MONITOR_SRC.glob("*.c"):
        for m in re.finditer(r'\.st_name\s*=\s*("([^"]+)"|(\w+_ST_NAME))', src.read_text(errors="replace")):
            names.add(m.group(2) or macros[m.group(3)])
    return names


def test_driver_map_covers_all_st_names():
    names = _st_names()
    assert len(names) >= 30, names
    for caps in driver_map.CAPABILITY_VARIANTS:
        for name in names:
            group = driver_map.group_for_type(name, caps)
            assert group in driver_map.DRIVER_GROUPS, (name, caps)
    assert names == set(driver_map.KNOWN_TYPES), sorted(names ^ set(driver_map.KNOWN_TYPES))


def test_driver_map_backend_resolution():
    x86 = {"arch": "x86_64", "configure": {"cpu_backend": "likwid", "ib_mad_dlopen": True,
                                            "infiniband": True}}
    arm = {"arch": "aarch64", "configure": {"cpu_backend": "dcgm", "ib_mad_dlopen": False,
                                             "infiniband": False}}
    assert driver_map.group_for_type("host_cpu_hw", x86) == "likwid"
    assert driver_map.group_for_type("host_cpu_hw", arm) == "dcgm_cpu"
    assert driver_map.group_for_type("host_ib", x86) == "ib_mad"
    assert driver_map.group_for_type("host_ib", arm) == "filesystem"
    assert driver_map.group_for_type("host_proc", x86) == "filesystem"
    assert driver_map.group_for_type("arm_aarch64_imc", arm) == "perf_event"
    assert driver_map.group_for_type("intel_x86_rapl", None) == "likwid"
    with pytest.raises(driver_map.UnmappedTypeError):
        driver_map.groups_for_enabled(["host_cpu", "brand_new_collector"], x86)
    groups = driver_map.groups_for_enabled(["host_cpu", "host_cpu_hw", "intel_x86_rapl"], x86)
    assert groups == {"filesystem": ["host_cpu"], "likwid": ["host_cpu_hw", "intel_x86_rapl"]}


def test_driver_map_dcgm_split_cpu_from_gpu():
    """Round 1 of the CPU-seconds plan: on Grace the DCGM CPU backend (sysmon module, ~157
    core-s/h on c122-018) and the GPU collector shared one SOLO leg, so their costs could not
    be told apart. They are separate driver groups now."""
    arm = {"arch": "aarch64", "configure": {"cpu_backend": "dcgm"}}
    x86 = {"arch": "x86_64", "configure": {"cpu_backend": "likwid"}}
    assert driver_map.group_for_type("host_cpu_hw", arm) == "dcgm_cpu"
    assert driver_map.group_for_type("host_cpu_hw", None) == "likwid"
    assert driver_map.group_for_type("host_cpu_hw", {"arch": "aarch64"}) == "dcgm_cpu"
    assert driver_map.group_for_type("nvidia_gpu", x86) == "dcgm_gpu"
    assert "dcgm" not in driver_map.DRIVER_GROUPS
    assert {"dcgm_cpu", "dcgm_gpu"} <= set(driver_map.DRIVER_GROUPS)
    groups = driver_map.groups_for_enabled(["host_cpu_hw", "nvidia_gpu", "host_mem"], arm)
    assert groups == {"dcgm_cpu": ["host_cpu_hw"], "dcgm_gpu": ["nvidia_gpu"],
                      "filesystem": ["host_mem"]}


def test_disable_csv_for_legs():
    active = ["host_cpu", "host_cpu_hw", "host_mem", "intel_x86_rapl"]
    groups = {"filesystem": ["host_cpu", "host_mem"], "likwid": ["host_cpu_hw", "intel_x86_rapl"]}
    assert driver_map.disable_csv("NONE", active, groups) == ",".join(sorted(active))
    assert driver_map.disable_csv("SOLO", active, groups, group="likwid") == "host_cpu,host_mem"
    assert driver_map.disable_csv("ALL", active, groups) is None
    assert driver_map.disable_csv("OFF", active, groups) is None


def test_schedule_profile_leg_is_last_and_budgeted():
    base = schedule.build_schedule(["filesystem"], blocks=1, seed=1)
    prof = schedule.build_schedule(["filesystem"], blocks=1, seed=1, profile_collectors=True)
    last = prof["legs"][-1]
    assert last["kind"] == "PROFILE" and last["fwq"] is False and last["leg_id"] == "b1-PROFILE-"
    extra = prof["projected_seconds"] - base["projected_seconds"]
    assert extra == prof["leg_seconds"] + prof["settle_seconds"] + schedule.PROFILER_BUILD_SECONDS
