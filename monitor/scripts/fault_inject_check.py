#!/usr/bin/env python3
"""Live fault-injection run against a DEBUG daemon (driven by fault_inject_verify.sh).

plan     pick the injectable fault points this node has, write the plan JSON and print the
         debug_fault_inject spec (empty when none applies)
capture  copy the current `fast` / `full` samples as baselines, run the restart command, then
         copy every new sample of either mirror until the plan's cycles are covered, plus the
         sentinel_sites and fault_fired files
check    the forced codes appear on exactly the planned keys, values resume after release
         where the read path does not latch, bridgeable gaps keep their counters monotonic,
         and every registered failure site fired unless its collector is inactive here; a
         planned point with 0 fires in fault_fired is a NOTE

Plan windows count collect cycles. Each cycle writes one sample: `full` (every key) every
sample_freq_slow, `fast` (fast-tier keys) otherwise; without the slow tier every sample is
`full`. So the capture takes both mirrors, numbers their samples by header timestamp
(sample_cycles), and a forced code counts when either mirror shows it inside the window. A
collect-time fault arms after ARM_DELAY clean cycles (`@delay+ticks`), so the rows it must mark
already exist; perf_open faults act at begin and hold for the whole run. The opendir fault on
/sys/class/infiniband runs last: it stops the port walk (no MAD query is reached) and the
directory latch holds it until the restart, so it starts after every other host_ib fault has
been checked for resume. mad_query plans the switch keys, plus the extended keys unless sysfs
counters/ serve them on every port (then the MAD extended query never runs; a node whose switch
MAD port is also absent never reaches the point: NOTE).

nvidia_gpu gets both GPU fault points, since only the run shows which source serves the node:
nvml_fetch first (a failed GPM sample hands the GPUs to the DCGM path for the rest of the run),
then dcgm_fetch after the nvml_fetch resume check, so on an NVML+GPM node it fires on that
fallback. A fired nvml_fetch passes when, after release, every GPU row is still emitted and reads
values not all frozen at the pre-fault reading, or carries BACKEND_ERROR on every key (DCGM
unusable here); the DCGM path's NOT_PRESENT / READ_FAILED / INVALID_VALUE keys (GPM-only
tensor_dmma_active, fields its watch profile lacks) are then not out of plan.

Output is bounded (--max-lines); the full report goes to --report.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

MONITOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MONITOR / "scripts"))

from lib.cross_sample_validate import check_sentinel_bridges  # noqa: E402
from lib.fault_ci_files import CI_COVERED_FILES  # noqa: E402
from lib.host_live_probes import IB_EXT_SYSFS_FILES, ib_port_ext_from_sysfs, probe_ib_devices  # noqa: E402
from lib.message_parse import parse_schema_counts, schema_key_name  # noqa: E402
from lib.payload_parse import iter_metric_rows, row_key_names, sample_header_timestamp  # noqa: E402
from lib.row_validate import token_is_uint, token_sentinel_code  # noqa: E402
from lib.sentinel_catalog import SENTINEL_SLUGS, sentinels_banner_version  # noqa: E402
from lib.sentinel_validate import sentinel_keys  # noqa: E402
from lib.shm_snapshot import SnapshotPair, read_shm_sample  # noqa: E402


def _code(slug: str) -> int:
    return next(n for n, s in SENTINEL_SLUGS.items() if s == slug)


NOT_UPDATED = _code("NOT_UPDATED")
READ_FAILED = _code("READ_FAILED")
BACKEND_ERROR = _code("BACKEND_ERROR")
INVALID_VALUE = _code("INVALID_VALUE")
BACKOFF = _code("BACKOFF")
PMU_BUSY = _code("PMU_BUSY")
NOT_PRESENT = _code("NOT_PRESENT")
EBUSY = 16
ARM_DELAY = 1  # clean collect cycles before a collect-time fault fires
RESUME_AFTER = 2  # released cycles before the resume check
KINDS = ("fast", "full")
POLL_SEC = 0.5
# Why a planned point may never run on a node that has its collector.
_NOT_REACHED_HINT = {"dcgm_fetch": "NVML+GPM serves the GPUs and DCGM never fetched",
                     "nvml_fetch": "DCGM serves the GPUs (not every GPU has GPM)",
                     "mad_query": "sysfs counters/ serve every port's extended packet keys and "
                                  "the switch MAD port is absent"}

# A mad_query fault reaches the extended query's keys (run only on ports without every counters/
# packet file, ib_family.c ib_ext_collect_sysfs) and the switch query's.
_IB_MAD_EXT_KEYS = ["port_select", "counter_select", *IB_EXT_SYSFS_FILES]
_IB_MAD_SW_KEYS = ["sw_rx_bytes", "sw_rx_packets", "sw_tx_bytes", "sw_tx_packets"]

# Collector types whose failure sites live in each source file (the site registry names files).
_FILE_TYPES = {
    "cpu.c": ["host_cpu"],
    "ps.c": ["host_ps"],
    "vfs.c": ["host_vfs"],
    "proc.c": ["host_proc"],
    "net.c": ["host_net"],
    "tmpfs.c": ["host_tmpfs"],
    "llite.c": ["lustre_llite"],
    "osc.c": ["lustre_osc"],
    "mdc.c": ["lustre_mdc"],
    "beegfs.c": ["beegfs_client"],
    "ib_family.c": ["host_ib"],
    "opa_sysfs.c": ["host_opa"],
    "nvidia_gpu.c": ["nvidia_gpu"],
    "intel_gpu.c": ["intel_gpu"],
    "intel_gpu_xpumcli.c": ["intel_gpu"],
    "amd_gpu.c": ["amd_gpu"],
    "arm_power_socket.c": ["host_power_socket"],
    "cpu_counter_metrics.c": ["host_cpu_hw"],
    "cpu_counter_metrics_arm_publish.c": ["host_cpu_hw"],
    "cpu_counter_metrics_x86_publish.c": ["host_cpu_hw"],
    "x86_uncore_perf.c": [f"intel_x86_uncore_imc_{g}" for g in ("skx", "icx", "spr", "emr", "gnr", "srf")]
    + [f"amd_x86_uncore_df_{g}" for g in ("rome", "milan", "genoa", "turin")],
    "intel_rapl.c": ["intel_x86_rapl"],
    "amd64_rapl.c": ["amd_x86_rapl"],
}


def _schema(manifest: dict) -> dict[str, list[str]]:
    return {t: info.get("schema_keys", []) for t, info in manifest.get("types", {}).items()}


def _ib_mad_fault_keys(ib_dir: Path) -> list[str]:
    """Keys a mad_query fault may mark: the switch keys, plus the extended keys unless sysfs
    serves them on every collectible port (no port: keep them, nothing says sysfs serves)."""
    ports = [ib_dir / hca / "ports" / port
             for hca, _, port in (d.rpartition(".") for d in probe_ib_devices(str(ib_dir)))]
    ext = [] if ports and all(map(ib_port_ext_from_sysfs, ports)) else _IB_MAD_EXT_KEYS
    return ext + _IB_MAD_SW_KEYS


def build_plan(caps: dict, manifest: dict, *, ticks: int, sys_root: Path) -> list[dict]:
    """Fault points present on this node, each with its spec entry, type, keys and codes."""
    schema = _schema(manifest)
    tokens = set(str(caps.get("capability_slug", "")).split("-"))
    backend = (caps.get("configure") or {}).get("cpu_backend")
    ib_dir = sys_root / "class" / "infiniband"
    has_ib = ib_dir.is_dir() and any(not d.name.startswith("hfi1") for d in ib_dir.iterdir())
    plan: list[dict] = []

    def add(point: str, type_name: str, keys: list[str], codes: list[int], resume: bool, *,
            delay: int | None = ARM_DELAY, n: int = ticks, also: tuple[int, ...] = (),
            fallback: bool = False) -> None:
        """delay None: active from begin until the restart (perf_open). `also`: codes the
        fault's aftermath may leave on its keys besides the forced ones. `fallback`: the
        release is judged by fallback_check (another source takes over) instead of resume."""
        spec = point if delay is None else f"{point}@{delay}+{n}" if delay else f"{point}@{n}"
        plan.append({"spec": spec, "type": type_name, "keys": keys, "codes": codes, "resume": resume,
                     "delay": delay, "ticks": n, "also": list(also), "fallback": fallback})

    def after_resume(type_name: str) -> int:
        """Delay whose window opens past every planned `type_name` resume check and its slack."""
        return max((resume_index(e) for e in plan if e["type"] == type_name), default=0) + 2

    if "host_ps" in schema:
        add("open:/proc/loadavg", "host_ps",
            ["load_1", "load_5", "load_15", "nr_running", "nr_threads"], [READ_FAILED], True)
    if "host_ib" in schema and has_ib and tokens & {"ib", "ibdyn"}:
        # Fewer ticks than the MAD fail threshold: BACKEND_ERROR, BACKOFF only on a long run.
        add("mad_query", "host_ib", _ib_mad_fault_keys(ib_dir), [BACKEND_ERROR, BACKOFF], True)
    if "nvidia_gpu" in schema:
        add("nvml_fetch", "nvidia_gpu", ["*"], [BACKEND_ERROR], True,
            also=(NOT_PRESENT, READ_FAILED, INVALID_VALUE), fallback=True)
        add("dcgm_fetch", "nvidia_gpu", ["*"], [BACKEND_ERROR], True, delay=after_resume("nvidia_gpu"))
    if "host_power_socket" in schema:
        add("hwmon_read", "host_power_socket", ["*"], [READ_FAILED], True)
    if "host_cpu_hw" in schema and backend == "arm":
        # Opens happen at begin / SIGHUP only, so the code holds until the restart that ends the run.
        add(f"perf_open:fp_dp_spec!{EBUSY}", "host_cpu_hw", ["arm_fp_dp_spec"], [PMU_BUSY], False,
            delay=None)
    if "host_cpu_hw" in schema and backend == "x86":
        # x86_core_perf "instructions": instr_retired (+ instr_retired_any on Intel). On AMD the
        # sysfs alias resolves to the retired_instructions raw event, which inherits the failed open.
        add(f"perf_open:instructions!{EBUSY}", "host_cpu_hw",
            ["instr_retired", "instr_retired_any", "retired_instructions"], [PMU_BUSY], False,
            delay=None)
    if "host_ib" in schema and has_ib:
        # The failed walk marks every host_ib key; the directory latch then answers ENOENT
        # (NOT_PRESENT) until the restart, so the fault needs one cycle and has no resume check.
        # It starts two cycles after the last host_ib resume check, past that window's slack.
        add("opendir:/sys/class/infiniband", "host_ib", ["*"], [READ_FAILED], False,
            delay=after_resume("host_ib"), n=1, also=(NOT_PRESENT,))
    return plan


def fault_window(entry: dict) -> tuple[int, int | None]:
    """Capture indices [lo, hi) where the fault fires, one cycle of slack on each side for a
    collect the capture did not see (perf_open entries: lo 0, hi None = the whole run)."""
    delay = entry.get("delay", 0)
    if delay is None:
        return 0, None
    return max(0, delay - 1), delay + entry["ticks"] + 1


def resume_index(entry: dict) -> int:
    """Capture index of the resume check: RESUME_AFTER cycles after release."""
    return (entry.get("delay") or 0) + entry["ticks"] + RESUME_AFTER


def capture_count(plan: list[dict]) -> int:
    """Samples a capture needs so every window and resume check falls inside it."""
    ends = [resume_index(e) + 1 for e in plan if e["resume"]]
    ends += [fault_window(e)[1] for e in plan if fault_window(e)[1] is not None]
    return max(ends, default=RESUME_AFTER + 1)


def sample_cycles(stamps: list[float]) -> list[int]:
    """Collect cycle of each ascending sample timestamp (the first is cycle 0). A gap counts
    round(gap / median gap) cycles, at least one, so a cycle without a captured sample does not
    shift the later ones."""
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    step = statistics.median(gaps) if gaps else 1.0
    cycles = [0]
    for gap in gaps:
        cycles.append(cycles[-1] + max(1, round(gap / step)))
    return cycles


def capture(shm_dir: Path, out_dir: Path, *, restart_cmd: str, plan: list[dict], timeout: float) -> None:
    """Baselines (baseline.<kind>), restart, then every new sample of either mirror as
    t<n>.<kind> (n in arrival order) until capture_count(plan) cycles are in and, once a `fast`
    sample showed the slow tier is on, a `full` one at or after the last resume check (slow-tier
    keys resume only there); then schema, sentinel_sites and fault_fired. `timeout` bounds the
    wait for each new sample."""
    out_dir.mkdir(parents=True, exist_ok=True)
    prev = dict.fromkeys(KINDS, 0.0)
    for kind in KINDS:
        if kind == "full" or (shm_dir / kind).is_file():
            ts, body, _ = read_shm_sample(shm_dir / kind)
            (out_dir / f"baseline.{kind}").write_text(body, encoding="utf-8")
            prev[kind] = ts if ts is not None else 0.0
    count = capture_count(plan)
    full_after = max((resume_index(e) for e in plan if e["resume"]), default=-1)
    subprocess.run(restart_cmd, shell=True, check=True)
    stamps: set[float] = set()
    fast_seen, last_full = False, -1
    deadline = time.time() + timeout
    while len(stamps) < count or (fast_seen and last_full < full_after):
        new = False
        for kind in KINDS:
            if not (shm_dir / kind).is_file():
                continue
            ts, body, _ = read_shm_sample(shm_dir / kind)
            if ts is None or ts <= prev[kind]:
                continue
            prev[kind], new = ts, True
            stamps.add(ts)
            (out_dir / f"t{len(stamps) - 1}.{kind}").write_text(body, encoding="utf-8")
            fast_seen = fast_seen or kind == "fast"
            last_full = len(stamps) - 1 if kind == "full" else last_full
        if new:
            deadline = time.time() + timeout
        elif time.time() > deadline:
            raise TimeoutError(f"no new fast/full sample within {timeout:.0f}s "
                               f"({len(stamps)} of {count} cycles captured)")
        else:
            time.sleep(POLL_SEC)
    for name in ("schema", "sentinel_sites", "fault_fired"):
        if name == "schema" or (shm_dir / name).is_file():
            shutil.copyfile(shm_dir / name, out_dir / name)


def _keys_of(entry: dict, schema: dict[str, list[str]]) -> set[str]:
    if entry["keys"] == ["*"]:
        return {schema_key_name(k) for k in schema[entry["type"]]}
    return set(entry["keys"])


def read_fault_fired(path: Path) -> dict[str, int] | None:
    """spec -> fires from the daemon's fault_fired lines (`<spec> <fires>`); None when the file
    is missing (a daemon older than the export)."""
    if not path.is_file():
        return None
    fired: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        spec, _, n = line.rpartition(" ")
        if spec and n.isdigit():
            fired[spec] = int(n)
    return fired


def not_reached(plan: list[dict], fired: dict[str, int] | None) -> set[str]:
    """Specs of planned entries whose fault point the daemon reports it never reached."""
    return {e["spec"] for e in plan if fired is not None and fired.get(e["spec"]) == 0}


def _tier_args(body: str, kind: str) -> dict:
    """Row-parse tier arguments: tier markers are required when the sample carries them."""
    tier = f"@{kind}" if f" @{kind}" in body else None
    return {"require_tier": tier is not None, "allowed_tier": tier}


def _sentinels(body: str, kind: str, schema: dict[str, list[str]]) -> dict[tuple[str, str, str], int]:
    return sentinel_keys(body, schema, **_tier_args(body, kind))


def _type_rows(body: str, kind: str, schema: dict[str, list[str]],
               type_name: str) -> dict[str, dict[str, str]]:
    """dev -> {key: token} of every `type_name` row in one sample."""
    return {row.dev: dict(zip(row_key_names(row, schema), row.values))
            for row in iter_metric_rows(body, schema, **_tier_args(body, kind)) if row.type_name == type_name}


def fallback_check(entry: dict, plan: list[dict], samples: list[tuple],
                   schema: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """Release check of a fired fault whose source hands its rows to another one (nvml_fetch:
    the DCGM path). Samples from cycle resume_index(entry) on, outside the other windows of the
    type, must show every row seen before then in each `full` sample, and each row must read
    values that are not all equal to its last reading before the fault armed, or carry
    BACKEND_ERROR on every key in every such sample (the other source is unusable here)."""
    type_name, spec, start = entry["type"], entry["spec"], resume_index(entry)
    others = [fault_window(e) for e in plan if e is not entry and e["type"] == type_name]
    rows = [(c, kind, _type_rows(body, kind, schema, type_name)) for c, _, kind, body, _ in samples]
    after = [(kind, r) for c, kind, r in rows
             if c >= start and not any(lo <= c and (hi is None or c < hi) for lo, hi in others)]
    devs = {d for c, _, r in rows if c < start for d in r}
    fulls = [set(r) for kind, r in after if kind == "full"]
    if not fulls:
        return [], [f"FAIL fault_inject {spec}: no full sample at or after cycle {start} outside the "
                    f"other {type_name} fault windows"]
    gone = sorted(devs - set.intersection(*fulls))
    if gone:
        return [], [f"FAIL fault_inject {spec}: {type_name} row(s) gone after the fallback: "
                    + ", ".join(gone[:8])]
    pre: dict[str, dict[str, str]] = {}
    for c, _, r in rows:
        if c < entry["delay"]:
            for d, toks in r.items():
                pre.setdefault(d, {}).update((k, v) for k, v in toks.items() if token_is_uint(v))
    errors: list[str] = []
    valued = blocked = 0
    for d in sorted(devs):
        seen = [r[d] for _, r in after if d in r]
        read = [(k, v) for toks in seen for k, v in toks.items() if token_is_uint(v)]
        if not read:
            if all(token_sentinel_code(v) == BACKEND_ERROR for toks in seen for v in toks.values()):
                blocked += 1
            else:
                errors.append(f"FAIL fault_inject {spec}: {type_name}/{d} has neither values nor "
                              "BACKEND_ERROR after the fallback")
            continue
        compared = [(k, v) for k, v in read if k in pre.get(d, {})]
        if compared and all(pre[d][k] == v for k, v in compared):
            errors.append(f"FAIL fault_inject {spec}: {type_name}/{d} values frozen at the pre-fault "
                          "reading after the fallback")
        else:
            valued += 1
    if errors:
        return [], errors
    note = f"PASS fault_inject {spec}: {valued} {type_name} row(s) read values after the fallback"
    return [note + (f", {blocked} BACKEND_ERROR (fallback source unusable here)" if blocked else "")], []


def _samples(cap_dir: Path) -> list[tuple[int, float, str, str]]:
    """(cycle, timestamp, kind, body) of every captured t<n>.<kind> sample, in time order."""
    got = []
    for path in cap_dir.glob("t*"):
        kind = path.suffix[1:]
        body = path.read_text(encoding="utf-8")
        ts = sample_header_timestamp(body)
        if kind in KINDS and ts is not None:
            got.append((ts, kind, body))
    got.sort(key=lambda s: s[0])
    stamps = sorted({ts for ts, _, _ in got})
    cycle_of = dict(zip(stamps, sample_cycles(stamps)))
    return [(cycle_of[ts], ts, kind, body) for ts, kind, body in got]


def check(plan: list[dict], manifest: dict, cap_dir: Path, *, sample_freq: float) -> tuple[list[str], list[str]]:
    """Notes and errors for one captured run."""
    notes: list[str] = []
    errors: list[str] = []
    schema = _schema(manifest)
    schema_body = (cap_dir / "schema").read_text(encoding="utf-8")
    schema.update({t: k for t, k in parse_schema_counts(schema_body).items() if t in schema})
    samples = [(c, ts, kind, body, _sentinels(body, kind, schema))
               for c, ts, kind, body in _samples(cap_dir)]
    need = capture_count(plan)
    cycles = samples[-1][0] + 1 if samples else 0
    if cycles < need:
        return notes, [f"FAIL fault_inject: {cycles} collect cycles captured, need {need}"]
    baseline: dict[tuple[str, str, str], set[int]] = {}
    for kind in KINDS:
        path = cap_dir / f"baseline.{kind}"
        if path.is_file():
            for key, c in _sentinels(path.read_text(encoding="utf-8"), kind, schema).items():
                baseline.setdefault(key, set()).add(c)
    window_end = max([hi for _, hi in map(fault_window, plan) if hi is not None], default=0)
    unreached = not_reached(plan, read_fault_fired(cap_dir / "fault_fired"))

    expected: dict[tuple[str, str], set[int]] = {}
    for entry in plan:
        # A fault point that never ran leaves no aftermath.
        also = [] if entry["spec"] in unreached else entry.get("also", [])
        for key in _keys_of(entry, schema):
            expected.setdefault((entry["type"], key), set()).update(entry["codes"] + also)
    for entry in plan:
        if entry["spec"] in unreached:
            hint = _NOT_REACHED_HINT.get(re.split(r"[:!@]", entry["spec"], maxsplit=1)[0])
            notes.append(f"NOTE fault_inject {entry['spec']}: fault point not reached on this node "
                         f"(0 fires{'; e.g. ' + hint if hint else ''})")
            continue
        keys = _keys_of(entry, schema)
        lo, hi = fault_window(entry)
        hit = {(t, d, k) for c_, _, _, _, m in samples if lo <= c_ and (hi is None or c_ < hi)
               for (t, d, k), c in m.items() if t == entry["type"] and k in keys and c in entry["codes"]}
        if not hit:
            errors.append(f"FAIL fault_inject {entry['spec']}: no {entry['type']} key carried "
                          + "/".join(SENTINEL_SLUGS[c] for c in entry["codes"]))
        else:
            notes.append(f"PASS fault_inject {entry['spec']}: {len(hit)} key(s)")
        if entry.get("fallback"):
            f_notes, f_errors = fallback_check(entry, plan, samples, schema)
            notes.extend(f_notes)
            errors.extend(f_errors)
        elif entry["resume"]:
            # First sample of each mirror after release; only `full` carries the slow-tier keys.
            after: dict[str, dict] = {}
            for c_, _, kind, _, m in samples:
                if c_ >= resume_index(entry):
                    after.setdefault(kind, m)
            if "full" not in after:
                errors.append(f"FAIL fault_inject {entry['spec']}: no full sample at or after cycle "
                              f"{resume_index(entry)}")
            stuck = sorted({f"{d} {k}" for m in after.values() for (t, d, k), c in m.items()
                            if t == entry["type"] and k in keys and c in entry["codes"]})
            if stuck:
                errors.append(f"FAIL fault_inject {entry['spec']}: no value after release: " + ", ".join(stuck[:8]))
    for c_, _, _, _, m in samples:
        if c_ >= window_end:
            continue
        for (t, d, k), c in m.items():
            # NOT_UPDATED is transient around the restart; persistence is its own layer.
            if c != NOT_UPDATED and c not in baseline.get((t, d, k), ()) and c not in expected.get((t, k), set()):
                errors.append(f"FAIL fault_inject: {t}/{d} {k} changed to {SENTINEL_SLUGS.get(c, c)}"
                              " outside the injected points")
    for kind in KINDS:
        series = [(ts, body) for _, ts, k, body, _ in samples if k == kind]
        if len(series) < 2:
            continue
        pair = SnapshotPair(kind=kind, ts_a=series[0][0], ts_b=series[-1][0], body_a=series[0][1],
                            body_b=series[-1][1], bodies_mid=tuple(b for _, b in series[1:-1]))
        b_notes, _, b_err = check_sentinel_bridges(pair, manifest, schema, sample_freq=sample_freq,
                                                   sentinel_banner=sentinels_banner_version(schema_body))
        notes.extend(n for n in b_notes if "sentinel" in n)
        errors.extend(b_err)
    return notes, errors


def check_sites(sites_path: Path, manifest: dict) -> tuple[list[str], list[str]]:
    """FAIL every registered site never fired, except: NOTE when make check forces every site of
    its file (fault_ci_files.CI_COVERED_FILES) or when its collector is not active here."""
    notes: list[str] = []
    errors: list[str] = []
    if not sites_path.is_file():
        return notes, ["FAIL fault_inject: no sentinel_sites file (not a DEBUG daemon?)"]
    active = set(manifest.get("types", {}))
    for line in sites_path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 2 or parts[1] != "0":
            continue
        base = parts[0].rsplit(":", 1)[0]
        types = _FILE_TYPES.get(base)
        if base in CI_COVERED_FILES:
            notes.append(f"NOTE fault_inject site {parts[0]} never fired (covered by CI matrix)")
        elif types is not None and not active & set(types):
            notes.append(f"NOTE fault_inject site {parts[0]} never fired (collector {'/'.join(types)} not active on this node)")
        else:
            errors.append(f"FAIL fault_inject site {parts[0]} never fired")
    return notes, errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--capabilities", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--ticks", type=int, default=3)
    p.add_argument("--sys-root", type=Path, default=Path("/sys"))
    p.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("capture")
    c.add_argument("--shm-dir", type=Path, required=True)
    c.add_argument("--out-dir", type=Path, required=True)
    c.add_argument("--restart-cmd", required=True)
    c.add_argument("--plan", type=Path, required=True)
    c.add_argument("--timeout", type=float, default=120.0)
    k = sub.add_parser("check")
    k.add_argument("--plan", type=Path, required=True)
    k.add_argument("--manifest", type=Path, required=True)
    k.add_argument("--capture-dir", type=Path, required=True)
    k.add_argument("--sample-freq", type=float, default=30.0)
    k.add_argument("--report", type=Path, default=None)
    k.add_argument("--max-lines", type=int, default=40)
    args = ap.parse_args(argv)

    if args.cmd == "plan":
        caps = json.loads(args.capabilities.read_text(encoding="utf-8"))
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        plan = build_plan(caps, manifest, ticks=args.ticks, sys_root=args.sys_root)
        args.out.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        print(",".join(e["spec"] for e in plan))
        return 0
    if args.cmd == "capture":
        try:
            plan = json.loads(args.plan.read_text(encoding="utf-8"))
            capture(args.shm_dir, args.out_dir, restart_cmd=args.restart_cmd, plan=plan,
                    timeout=args.timeout)
        except (OSError, TimeoutError, ValueError, subprocess.CalledProcessError) as exc:
            print(f"FAIL fault_inject capture: {exc}")
            return 1
        return 0
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    notes, errors = check(plan, manifest, args.capture_dir, sample_freq=args.sample_freq)
    s_notes, s_err = check_sites(args.capture_dir / "sentinel_sites", manifest)
    lines = notes + s_notes + errors + s_err
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    shown = lines[: args.max_lines]
    print("\n".join(shown))
    if len(lines) > len(shown):
        print(f"... {len(lines) - len(shown)} more line(s) in the report")
    failed = errors + s_err
    if not failed and plan and len(not_reached(plan, read_fault_fired(args.capture_dir / "fault_fired"))) == len(plan):
        print("fault_inject: NOT_RUN (no planned fault point was reached on this node)")
        return 0
    print(f"fault_inject: {'FAIL' if failed else 'PASS'} ({len(failed)} failure(s))")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
