"""Bracketed, replicated leg schedule (decision 10) with budget auto-fit.

Block b: OFF → NONE → ALL → SOLO(D…) and VARIANT(V…) in seeded random order → ALL → OFF, and
the closing OFF is shared with block b+1. A VARIANT leg (driver_map.VARIANTS) runs a base group
with one change and is paired with that base group's SOLO leg in the same block. Headline legs (OFF/NONE/ALL) add the FWQ phase. When the projection
exceeds ``max_hours``, reductions apply in a fixed order and each is recorded in
``fit_steps``: (1) shorten legs 1800 → 1200 → 600 s, (2) drop the second ALL leg per block,
(3) run SOLO legs only in the first block. If it still does not fit, ScheduleDoesNotFit carries
the minimum hours so the operator can raise ``--max-hours``.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from typing import Any

LEG_SHORTENINGS = (1200, 600)
# --profile-collectors: build the --enable-metric-profiler binary from the dist tarball.
PROFILER_BUILD_SECONDS = 900
FIT_ORDER = ("shorten_legs", "drop_second_all", "solo_single_block")


class ScheduleDoesNotFit(ValueError):
    def __init__(self, min_hours: float, max_hours: float):
        self.min_hours = min_hours
        super().__init__(f"schedule needs at least {min_hours:.2f} h (minimum after every fit "
                         f"step) but --max-hours is {max_hours:g}; raise --max-hours")


def _leg(kind: str, block: int, group: str | None = None, **extra: Any) -> dict[str, Any]:
    leg = {"kind": kind, "block": block, "group": group, "fwq": kind in ("OFF", "NONE", "ALL")}
    leg.update(extra)
    tag = group or extra.get("sample_freq") or extra.get("position") or ""
    leg["leg_id"] = f"b{block}-{kind}-{tag}"
    return leg


def _legs(groups: Sequence[str], blocks: int, seed: int, off_legs: bool, second_all: bool,
          solo_blocks: int, cadence_sweep: Sequence[float],
          profile_collectors: bool = False,
          variants: Mapping[str, Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    variants = variants or {}
    rng = random.Random(seed)
    legs: list[dict[str, Any]] = []
    if off_legs:
        legs.append(_leg("OFF", 1, brackets=[1]))
    for b in range(1, blocks + 1):
        legs.append(_leg("NONE", b))
        legs.append(_leg("ALL", b, position="first"))
        if b <= solo_blocks:
            order = list(groups) + [v for v in variants if variants[v]["base"] in groups]
            rng.shuffle(order)
            legs.extend(_leg("VARIANT", b, g, **{k: variants[g][k] for k in ("base", "env", "marker")})
                        if g in variants else _leg("SOLO", b, g) for g in order)
        if second_all:
            legs.append(_leg("ALL", b, position="second"))
        if off_legs:
            legs.append(_leg("OFF", b + 1 if b < blocks else b,
                             brackets=[b, b + 1] if b < blocks else [b]))
    if off_legs:
        # The closing OFF of the last block keeps its own id so ids stay unique.
        legs[-1]["leg_id"] = f"b{blocks}-OFF-end"
    for freq in cadence_sweep:
        legs.append(_leg("CADENCE", blocks, sample_freq=freq, fwq=False))
    if profile_collectors:
        # Instrumented build, ALL configuration, production queue; diagnostic only.
        legs.append(_leg("PROFILE", blocks, fwq=False))
    return legs


def _project(legs: list[dict[str, Any]], leg_seconds: int, settle: int, fwq_seconds: int,
             setup_seconds: int) -> int:
    per = sum(leg_seconds + settle + (fwq_seconds if leg["fwq"] else 0) for leg in legs)
    return per + setup_seconds


def build_schedule(groups: Sequence[str], blocks: int = 2, seed: int = 0,
                   leg_seconds: int = 1800, settle_seconds: int = 60, fwq_seconds: int = 300,
                   max_hours: float = 12.0, off_legs: bool = True,
                   cadence_sweep: Sequence[float] = (), setup_seconds: int = 1800,
                   profile_collectors: bool = False,
                   variants: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Return ``{legs, leg_seconds, projected_seconds, fit_steps, seed, ...}``."""
    budget = max_hours * 3600
    if profile_collectors:
        setup_seconds += PROFILER_BUILD_SECONDS
    state = {"leg_seconds": leg_seconds, "second_all": True, "solo_blocks": blocks}
    fit_steps: list[dict[str, Any]] = []

    def plan() -> tuple[list[dict[str, Any]], int]:
        legs = _legs(groups, blocks, seed, off_legs, state["second_all"], state["solo_blocks"],
                     cadence_sweep, profile_collectors, variants)
        return legs, _project(legs, state["leg_seconds"], settle_seconds, fwq_seconds,
                              setup_seconds)

    legs, total = plan()
    steps = [("shorten_legs", {"leg_seconds": s}) for s in LEG_SHORTENINGS if s < leg_seconds]
    steps.append(("drop_second_all", {"second_all": False}))
    if blocks > 1:
        steps.append(("solo_single_block", {"solo_blocks": 1}))
    for name, change in steps:
        if total <= budget:
            break
        state.update(change)
        legs, total = plan()
        fit_steps.append({"step": name, **change, "projected_hours": round(total / 3600, 3)})
    if total > budget:
        raise ScheduleDoesNotFit(total / 3600, max_hours)
    return {"legs": legs, "seed": seed, "blocks": blocks, "groups": list(groups),
            "variants": sorted({leg["group"] for leg in legs if leg["kind"] == "VARIANT"}),
            "leg_seconds": state["leg_seconds"], "settle_seconds": settle_seconds,
            "fwq_seconds": fwq_seconds, "setup_seconds": setup_seconds,
            "projected_seconds": total, "max_hours": max_hours, "fit_steps": fit_steps,
            "off_legs": off_legs}
