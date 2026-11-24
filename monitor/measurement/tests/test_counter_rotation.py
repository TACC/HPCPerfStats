"""CPU counter rotation cost (kernel perf multiplexing), from c573-002 2026-11-23.

Every leg with x86_perf on had ~0.39 interrupt cores and ~129,230 interrupts/s against ~0.0013
and ~1,170 without it: 1,000/s on each of 128 CPUs at 3.0 us. The cost is interrupt time on
every core, never daemon CPU, so the profile must report it on its own.
"""

from __future__ import annotations

from lib import counter_rotation


def _leg(leg_id: str, kind: str, block: int, irq: float, intr: float, group: str | None = None,
         status: str = "ok", contaminated: bool = False) -> dict:
    return {"leg_id": leg_id, "kind": kind, "block": block, "group": group, "status": status,
            "contaminated": contaminated, "node": {"irq_cores": irq, "intr_per_s": intr}}


def _turin_legs() -> list[dict]:
    legs = []
    for b in (1, 2, 3):
        legs += [_leg(f"b{b}-OFF-", "OFF", b, 0.0012, 1120),
                 _leg(f"b{b}-NONE-", "NONE", b, 0.0013, 1170),
                 _leg(f"b{b}-SOLO-x86_perf", "SOLO", b, 0.3900, 129230, "x86_perf"),
                 _leg(f"b{b}-SOLO-dcgm_gpu", "SOLO", b, 0.0014, 1180, "dcgm_gpu"),
                 _leg(f"b{b}-ALL-first", "ALL", b, 0.3905, 129240)]
    return legs


def test_turin_rotation_cost() -> None:
    rot = counter_rotation.summarize(_turin_legs(), 128)
    assert rot["status"] == "ok" and rot["group"] == "x86_perf"
    solo = rot["solo"]
    assert solo["n"] == 3
    assert abs(solo["irq_cores"] - 0.3887) < 1e-9
    assert abs(solo["intr_per_s"] - 128060) < 1e-6
    assert abs(solo["intr_per_s_per_cpu"] - 1000.47) < 0.01
    assert abs(solo["us_per_interrupt"] - 3.035) < 0.001
    assert rot["all"]["n"] == 3
    assert len(rot["pairs"]) == 6


def test_contaminated_and_failed_legs_are_left_out() -> None:
    legs = _turin_legs()
    legs[2] = {**legs[2], "contaminated": True}            # b1 SOLO x86_perf
    legs.append(_leg("b4-SOLO-x86_perf", "SOLO", 4, 9.0, 9e9, "x86_perf"))  # no NONE in b4
    legs.append(_leg("b5-NONE-", "NONE", 5, 0.0, 0.0, status="type_mismatch"))
    rot = counter_rotation.summarize(legs, 128)
    assert rot["solo"]["n"] == 2
    assert abs(rot["solo"]["irq_cores"] - 0.3887) < 1e-9


def test_grace_group_is_found() -> None:
    legs = [_leg("b1-NONE-", "NONE", 1, 0.001, 1000),
            _leg("b1-SOLO-arm_cpu", "SOLO", 1, 0.2, 73000, "arm_cpu")]
    rot = counter_rotation.summarize(legs, 144)
    assert rot["group"] == "arm_cpu" and rot["all"]["n"] == 0
    assert rot["all"]["irq_cores"] is None


def test_no_cpu_counter_leg_is_not_measured() -> None:
    legs = [leg for leg in _turin_legs() if leg.get("group") != "x86_perf"]
    rot = counter_rotation.summarize(legs, 128)
    assert rot["status"] == "NOT_MEASURED"
    assert counter_rotation.md_lines(rot)[-2].startswith("NOT_MEASURED")


def test_no_extra_interrupts_gives_no_per_interrupt_cost() -> None:
    legs = [_leg("b1-NONE-", "NONE", 1, 0.0013, 1170),
            _leg("b1-SOLO-x86_perf", "SOLO", 1, 0.0013, 1170, "x86_perf")]
    rot = counter_rotation.summarize(legs, 128)
    assert rot["solo"]["intr_per_s"] == 0 and rot["solo"]["us_per_interrupt"] is None


def test_report_section() -> None:
    lines = counter_rotation.md_lines(counter_rotation.summarize(_turin_legs(), 128))
    text = "\n".join(lines)
    assert "| SOLO - NONE | 3 | 0.3887 | 128,060 | 1,000.5 | 3.04 |" in text
    assert "not to the daemon" in text
