#!/usr/bin/env python3
"""Live fault-injection run against a DEBUG daemon (driven by fault_inject_verify.sh).

plan     pick the injectable fault points this node has, write the plan JSON and print the
         debug_fault_inject spec (empty when none applies)
capture  copy the current shm sample as the baseline, run the restart command, then copy as
         many consecutive samples as the plan needs and the sentinel_sites file
check    the forced codes appear on exactly the planned keys, values resume after release
         where the read path does not latch, bridgeable gaps keep their counters monotonic,
         and every registered failure site fired unless its collector is inactive here

Sample i of a capture is collect cycle i after the restart. A collect-time fault arms after
ARM_DELAY clean cycles (`@delay+ticks`), so the rows it must mark already exist; perf_open faults
act at begin and hold for the whole run. The opendir fault on /sys/class/infiniband runs last:
it stops the port walk (no MAD query is reached) and the directory latch holds it until the
restart, so it starts after every other host_ib fault has been checked for resume.

Output is bounded (--max-lines); the full report goes to --report.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

MONITOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MONITOR / "scripts"))

from lib.cross_sample_validate import check_sentinel_bridges  # noqa: E402
from lib.fault_ci_files import CI_COVERED_FILES  # noqa: E402
from lib.message_parse import parse_schema_counts, schema_key_name  # noqa: E402
from lib.sentinel_catalog import SENTINEL_SLUGS, sentinels_banner_version  # noqa: E402
from lib.sentinel_validate import sentinel_keys  # noqa: E402
from lib.shm_snapshot import SnapshotPair, read_shm_sample, wait_for_timestamp_advance  # noqa: E402


def _code(slug: str) -> int:
    return next(n for n, s in SENTINEL_SLUGS.items() if s == slug)


NOT_UPDATED = _code("NOT_UPDATED")
READ_FAILED = _code("READ_FAILED")
BACKEND_ERROR = _code("BACKEND_ERROR")
BACKOFF = _code("BACKOFF")
PMU_BUSY = _code("PMU_BUSY")
NOT_PRESENT = _code("NOT_PRESENT")
EBUSY = 16
ARM_DELAY = 1  # clean collect cycles before a collect-time fault fires
RESUME_AFTER = 2  # released cycles before the resume check

_IB_MAD_KEYS = (
    "port_select counter_select port_xmit_pkts port_rcv_pkts port_unicast_xmit_pkts "
    "port_unicast_rcv_pkts port_multicast_xmit_pkts port_multicast_rcv_pkts "
    "sw_rx_bytes sw_rx_packets sw_tx_bytes sw_tx_packets"
).split()

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


def build_plan(caps: dict, manifest: dict, *, ticks: int, sys_root: Path) -> list[dict]:
    """Fault points present on this node, each with its spec entry, type, keys and codes."""
    schema = _schema(manifest)
    tokens = set(str(caps.get("capability_slug", "")).split("-"))
    backend = (caps.get("configure") or {}).get("cpu_backend")
    ib_dir = sys_root / "class" / "infiniband"
    has_ib = ib_dir.is_dir() and any(not d.name.startswith("hfi1") for d in ib_dir.iterdir())
    plan: list[dict] = []

    def add(point: str, type_name: str, keys: list[str], codes: list[int], resume: bool, *,
            delay: int | None = ARM_DELAY, n: int = ticks, also: tuple[int, ...] = ()) -> None:
        """delay None: active from begin until the restart (perf_open). `also`: codes the
        fault's aftermath may leave on its keys besides the forced ones."""
        spec = point if delay is None else f"{point}@{delay}+{n}" if delay else f"{point}@{n}"
        plan.append({"spec": spec, "type": type_name, "keys": keys, "codes": codes, "resume": resume,
                     "delay": delay, "ticks": n, "also": list(also)})

    if "host_ps" in schema:
        add("open:/proc/loadavg", "host_ps",
            ["load_1", "load_5", "load_15", "nr_running", "nr_threads"], [READ_FAILED], True)
    if "host_ib" in schema and has_ib and tokens & {"ib", "ibdyn"}:
        # Fewer ticks than the MAD fail threshold: BACKEND_ERROR, BACKOFF only on a long run.
        add("mad_query", "host_ib", list(_IB_MAD_KEYS), [BACKEND_ERROR, BACKOFF], True)
    if "nvidia_gpu" in schema:
        add("dcgm_fetch", "nvidia_gpu", ["*"], [BACKEND_ERROR], True)
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
        start = max((resume_index(e) for e in plan if e["type"] == "host_ib"), default=0) + 2
        add("opendir:/sys/class/infiniband", "host_ib", ["*"], [READ_FAILED], False,
            delay=max(start, ARM_DELAY), n=1, also=(NOT_PRESENT,))
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


def capture(shm_dir: Path, out_dir: Path, *, restart_cmd: str, count: int, timeout: float) -> None:
    """Baseline, restart, then `count` consecutive `full` samples plus sentinel_sites."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = shm_dir / "full"
    ts, body, _ = read_shm_sample(path)
    (out_dir / "baseline").write_text(body, encoding="utf-8")
    subprocess.run(restart_cmd, shell=True, check=True)
    prev = ts if ts is not None else 0.0
    for i in range(count):
        prev, body = wait_for_timestamp_advance(path, prev, timeout_sec=timeout)
        (out_dir / f"t{i}").write_text(body, encoding="utf-8")
    shutil.copyfile(shm_dir / "schema", out_dir / "schema")
    if (shm_dir / "sentinel_sites").is_file():
        shutil.copyfile(shm_dir / "sentinel_sites", out_dir / "sentinel_sites")


def _keys_of(entry: dict, schema: dict[str, list[str]]) -> set[str]:
    if entry["keys"] == ["*"]:
        return {schema_key_name(k) for k in schema[entry["type"]]}
    return set(entry["keys"])


def check(plan: list[dict], manifest: dict, cap_dir: Path, *, sample_freq: float) -> tuple[list[str], list[str]]:
    """Notes and errors for one captured run."""
    notes: list[str] = []
    errors: list[str] = []
    schema = _schema(manifest)
    schema_body = (cap_dir / "schema").read_text(encoding="utf-8")
    schema.update({t: k for t, k in parse_schema_counts(schema_body).items() if t in schema})
    paths = sorted(cap_dir.glob("t[0-9]*"), key=lambda p: int(p.name[1:]))
    bodies = [p.read_text(encoding="utf-8") for p in paths]
    need = capture_count(plan)
    if len(bodies) < need:
        return notes, [f"FAIL fault_inject: {len(bodies)} captures, need {need}"]
    req = False
    tier = None
    if any(" @full" in ln for ln in bodies[0].splitlines()):
        req, tier = True, "@full"
    maps = [sentinel_keys(b, schema, require_tier=req, allowed_tier=tier) for b in bodies]
    baseline = sentinel_keys((cap_dir / "baseline").read_text(encoding="utf-8"), schema,
                             require_tier=req, allowed_tier=tier)
    window = maps[:max([hi for _, hi in map(fault_window, plan) if hi is not None], default=0)]

    expected: dict[tuple[str, str], set[int]] = {}
    for entry in plan:
        for key in _keys_of(entry, schema):
            expected.setdefault((entry["type"], key), set()).update(entry["codes"] + entry.get("also", []))
    for entry in plan:
        keys = _keys_of(entry, schema)
        lo, hi = fault_window(entry)
        hit = {(t, d, k) for m in maps[lo:hi] for (t, d, k), c in m.items()
               if t == entry["type"] and k in keys and c in entry["codes"]}
        if not hit:
            errors.append(f"FAIL fault_inject {entry['spec']}: no {entry['type']} key carried "
                          + "/".join(SENTINEL_SLUGS[c] for c in entry["codes"]))
        else:
            notes.append(f"PASS fault_inject {entry['spec']}: {len(hit)} key(s)")
        if entry["resume"]:
            stuck = sorted(f"{d} {k}" for (t, d, k), c in maps[resume_index(entry)].items()
                           if t == entry["type"] and k in keys and c in entry["codes"])
            if stuck:
                errors.append(f"FAIL fault_inject {entry['spec']}: no value after release: " + ", ".join(stuck[:8]))
    for m in window:
        for (t, d, k), c in m.items():
            # NOT_UPDATED is transient around the restart; persistence is its own layer.
            if c != NOT_UPDATED and baseline.get((t, d, k)) != c and c not in expected.get((t, k), set()):
                errors.append(f"FAIL fault_inject: {t}/{d} {k} changed to {SENTINEL_SLUGS.get(c, c)}"
                              " outside the injected points")
    pair = SnapshotPair(kind="full", ts_a=0.0, ts_b=sample_freq * (len(bodies) - 1),
                        body_a=bodies[0], body_b=bodies[-1], bodies_mid=tuple(bodies[1:-1]))
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
            capture(args.shm_dir, args.out_dir, restart_cmd=args.restart_cmd, count=capture_count(plan),
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
    print(f"fault_inject: {'FAIL' if failed else 'PASS'} ({len(failed)} failure(s))")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
