"""Per-driver cost table (C2) from ALL / SOLO / NONE / OFF legs, paired within each block.

framework           = NONE                        (daemon with zero collectors)
driver D            = SOLO_b(D) − NONE_b          (same block, so slow drift cancels)
interaction residual = ALL_b − [NONE_b + Σ_D (SOLO_b(D) − NONE_b)]
share of ALL        = (SOLO − NONE) / ALL         (process metrics: the OFF daemon cost is 0)

Replicate blocks are combined with ``stats.combine_replicates``. When a block's NONE leg did
not run (zero-collector daemon misbehaved), that block falls back to SOLO − OFF (= SOLO for
process metrics) and the row notes that it includes the framework cost.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from . import stats

# (key in leg["process"], column name, is_interval)
COLUMNS = (
    ("cpu_core_s_per_h", "cpu_core_s_per_h", True),
    ("ms_per_fast_sample", "ms_per_fast_sample", False),
    ("ms_per_full_sample", "ms_per_full_sample", False),
    ("uss_mib", "uss_mib_delta", False),
    ("broker_bytes_acked_per_h", "broker_bytes_per_h_delta", False),
    ("wakeups_per_s", "wakeups_per_s_delta", False),
    ("perf_event_fds", "perf_event_fds_delta", False),
    ("init_cpu_s", "init_cpu_s_delta", False),
)
RESIDUAL_FRACTION = 0.10


def _ok(leg: dict[str, Any] | None) -> bool:
    return (bool(leg) and leg.get("status") == "ok" and leg.get("process") is not None
            and (not leg.get("contaminated") or bool(leg.get("smoke_run"))))


def _as_ci(v: Any) -> dict[str, Any] | None:
    if v is None:
        return None
    if isinstance(v, dict):
        return v
    return {"mean": float(v), "lo": None, "hi": None}


def _value(leg: dict[str, Any], key: str) -> Any:
    return (leg.get("process") or {}).get(key)


def _blocks(legs: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for leg in legs:
        b = out.setdefault(leg["block"], {"NONE": None, "ALL": [], "SOLO": {}})
        if leg["kind"] == "NONE":
            b["NONE"] = leg
        elif leg["kind"] == "ALL" and _ok(leg):
            b["ALL"].append(leg)
        elif leg["kind"] == "SOLO":
            b["SOLO"][leg["group"]] = leg
    return out


def _all_ci(block: dict[str, Any], key: str) -> dict[str, Any] | None:
    vals = [_as_ci(_value(leg, key)) for leg in block["ALL"]]
    return stats.combine_replicates([v for v in vals if v is not None])


def _point(ci: dict[str, Any] | None) -> float | None:
    return None if ci is None else ci["mean"]


def build_matrix(legs: list[dict[str, Any]], groups: dict[str, list[str]],
                 active: list[str], interrupted: bool = False) -> dict[str, Any]:
    blocks = _blocks(legs)
    all_cpu = stats.combine_replicates(
        [c for c in (_all_ci(b, "cpu_core_s_per_h") for b in blocks.values()) if c])
    none_cpu = stats.combine_replicates(
        [_as_ci(_value(b["NONE"], "cpu_core_s_per_h")) for b in blocks.values() if _ok(b["NONE"])])

    rows = []
    for group, types in groups.items():
        row: dict[str, Any] = {"group": group, "types": list(types), "notes": [],
                               "status": "ok"}
        per_col: dict[str, list[dict[str, Any]]] = {col: [] for _, col, _ in COLUMNS}
        shares = []
        measured: set[str] = set()
        ran = False
        for bnum, block in sorted(blocks.items()):
            solo = block["SOLO"].get(group)
            if solo is None:
                continue
            ran = True
            if not _ok(solo):
                row["notes"].append(f"block {bnum}: SOLO leg status {solo.get('status')}")
                continue
            verified = [t for t in solo.get("verified_types", []) if t in types]
            if not verified:
                row["notes"].append(f"block {bnum}: none of {types} ran in isolation")
                continue
            measured.update(verified)
            if solo.get("missing_types"):
                row["notes"].append(f"block {bnum}: did not run in isolation: "
                                    + ", ".join(solo["missing_types"]))
            base = block["NONE"] if _ok(block["NONE"]) else None
            if base is None:
                row["notes"].append(f"block {bnum}: NONE leg missing; SOLO − OFF includes "
                                    "framework cost")
            for key, col, _ in COLUMNS:
                s = _as_ci(_value(solo, key))
                if s is None:
                    continue
                d = s if base is None else stats.diff_ci(s, _as_ci(_value(base, key)))
                if d is not None:
                    per_col[col].append(d)
            a = _all_ci(block, "cpu_core_s_per_h")
            if per_col["cpu_core_s_per_h"] and a and a["mean"]:
                shares.append(per_col["cpu_core_s_per_h"][-1]["mean"] / a["mean"])
        if not per_col["cpu_core_s_per_h"]:
            row["status"] = "NOT_MEASURED"
            if not ran:
                row["reason"] = "run interrupted" if interrupted else "no SOLO leg ran"
            else:
                bad = sorted({str(b["SOLO"][group].get("status")) for b in blocks.values()
                              if b["SOLO"].get(group) and not _ok(b["SOLO"][group])})
                row["reason"] = f"SOLO leg status {', '.join(bad)}" if bad else "init failed in isolation"
            rows.append(row)
            continue
        row["types_measured"] = sorted(measured)
        for _key, col, interval in COLUMNS:
            comb = stats.combine_replicates(per_col[col])
            if comb is None:
                row[col] = None
            elif interval:
                row[col] = {**comb, "n_blocks": len(per_col[col])}
            else:
                row[col] = comb["mean"]
        row["share_of_all"] = sum(shares) / len(shares) if shares else None
        if len(measured) < len(types):
            row["notes"].append("group cost covers only the types that ran")
        rows.append(row)
    rows.sort(key=lambda r: -(r["cpu_core_s_per_h"]["mean"] if r.get("cpu_core_s_per_h") else -1e18))

    residuals = []
    measured_rows = [r for r in rows if r["status"] == "ok"]
    for block in blocks.values():
        a = _all_ci(block, "cpu_core_s_per_h")
        if a is None or not _ok(block["NONE"]):
            continue
        n_ci = _as_ci(_value(block["NONE"], "cpu_core_s_per_h"))
        terms = [(1.0, a), (-1.0, n_ci)]
        complete = True
        for r in measured_rows:
            solo = block["SOLO"].get(r["group"])
            if not _ok(solo):
                complete = False
                break
            terms += [(-1.0, _as_ci(_value(solo, "cpu_core_s_per_h"))), (1.0, n_ci)]
        if complete:
            res = stats.linear_combination(terms)
            if res is not None:
                residuals.append(res)
    residual = stats.combine_replicates(residuals)
    large = False
    if residual is not None and all_cpu is not None:
        excludes_zero = residual["lo"] is None or residual["lo"] > 0 or residual["hi"] < 0
        large = abs(residual["mean"]) > RESIDUAL_FRACTION * abs(all_cpu["mean"]) and excludes_zero

    framework: dict[str, Any]
    if none_cpu is None:
        bad = sorted({str(b["NONE"].get("status")) for b in blocks.values() if b.get("NONE")})
        framework = {"status": "NOT_MEASURED",
                     "reason": f"NONE leg status {', '.join(bad)}" if bad
                     else "NONE leg did not run or the zero-collector daemon failed"}
    else:
        framework = {"status": "ok", "cpu_core_s_per_h": none_cpu}
    return {
        "rows": rows,
        "framework": framework,
        "all": {"cpu_core_s_per_h": all_cpu, "types": list(active)},
        "residual": ({"status": "NOT_MEASURED", "reason": "no block had NONE, ALL and every SOLO",
                      "large": False} if residual is None else
                     {"cpu_core_s_per_h": residual, "large": large,
                      "note": ("large interaction residual: cite per-driver numbers as "
                               "approximate") if large else None}),
        "method": "SOLO−NONE paired within block; replicates max(pooled, half-range)",
    }
