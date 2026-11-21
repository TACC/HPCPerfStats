#!/usr/bin/env python3
"""Counter accuracy: the installed DEBUG daemon's own counter deltas against known loads.

Driven by ``canary_counter_check.sh --accuracy`` (the counter-accuracy layer of
``rpm_debug_shm_verify.sh``). One *window* is two snapshots of the DEBUG ``/dev/shm`` ``fast``
mirror (first non-blank line is the header) with a bounded load between them; the second
snapshot is the first sample written after the load ended, so the daemon delta covers the whole
load. Checks per window (``WINDOW_CHECKS``):

- memory: read / write kernel exact bytes vs ``dram_cas_*`` (64 B) + ``hbm_cas_*`` (32 B) on the
  Intel IMC / HBM rows, AMD DF ``chan<N>`` row beats (64 B; Zen2 / Zen3 read + write combined,
  Zen4 / Zen5 split) or the
  Grace SCF ``dram_cas_*`` (64 B), summed over every row; on Grace the ``scf0`` share of a
  socket-0 pinned read (x86 rows sum every socket, so there is no per-socket row);
- FLOPs: GEMM exact FLOPs vs Intel ``fp_arith_inst_retired_*`` lane-weighted, AMD
  ``fp_ops_retired`` (Zen5 ``amd_fp_dp_ops`` DP split) or the Grace ``arm_fp_*_spec`` operation
  count (flops per op must not exceed the 128-bit SVE FMA ceiling);
- instructions: ``instr_retired`` (``instr_retired_any``) vs ``perf stat -a instructions`` over
  the window;
- energy (x86): ``pkg_energy`` / ``dram_energy`` (mJ, mod 2^32) vs powercap ``energy_uj``
  (package-N zones, dram subzones; mod max_energy_range_uj + 1).

A missing source (no PMU, a sentinel, no powercap, no perf, a load that cannot run) is NOT_RUN
with the reason, never PASS. A daemon whose ``fast`` payload stops advancing is FAIL. Results go
to a TSV (name, status, ratio, reason) that ``correctness_summary.py`` folds into the per-node
campaign JSON. Python 3.6 compatible (EL8 system python3).
"""

import argparse
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

# Accepted daemon / reference ratio per check family.
TOLERANCES = {
    "flops": (0.90, 1.10),
    "instructions": (0.95, 1.05),
    "memory": (0.75, 1.50),
    "energy": (0.95, 1.05),
    "socket0_share": (0.75, 1.00),
}
# Grace: GEMM FLOPs per arm_fp_*_spec operation must not exceed one 128-bit SVE FMA (1% slack).
GRACE_FLOPS_PER_OP_CEILING = {"dp": 4, "sp": 8, "hp": 16}
GRACE_CEILING_SLACK = 1.01

INTEL_FP_WEIGHTS = {
    "dp": {
        "fp_arith_inst_retired_scalar_double": 1,
        "fp_arith_inst_retired_128b_packed_double": 2,
        "fp_arith_inst_retired_256b_packed_double": 4,
        "fp_arith_inst_retired_512b_packed_double": 8,
    },
    "sp": {
        "fp_arith_inst_retired_scalar_single": 1,
        "fp_arith_inst_retired_128b_packed_single": 4,
        "fp_arith_inst_retired_256b_packed_single": 8,
        "fp_arith_inst_retired_512b_packed_single": 16,
    },
}

# (type prefix, {"read": ((key, bytes per count), ...), "write": ...}). Rows whose key carries a
# structural code (NOT_BUILT / NOT_PRESENT) in both snapshots are left out of the sum.
MEMORY_SOURCES = (
    ("intel_x86_uncore_imc_", {"read": (("dram_cas_reads", 64), ("hbm_cas_reads", 32)),
                               "write": (("dram_cas_writes", 64), ("hbm_cas_writes", 32))}),
    ("amd_x86_uncore_df_", {"read": (("dram_data_beats", 64), ("dram_read_beats", 64)),
                            "write": (("dram_write_beats", 64),)}),
    ("arm_aarch64_imc", {"read": (("dram_cas_reads", 64),), "write": (("dram_cas_writes", 64),)}),
)
RAPL_TYPES = ("intel_x86_rapl", "amd_x86_rapl")
NOT_BUILT = 10
NOT_PRESENT = 2

COUNTER_CHECKS = ("mem_read", "mem_write", "mem_socket0", "flops_dp", "flops_sp",
                  "instructions", "energy_pkg", "energy_dram")
WINDOW_CHECKS = {
    "memory-read": ("mem_read", "mem_socket0"),
    "memory-write": ("mem_write",),
    "gemm-dp": ("flops_dp", "instructions", "energy_pkg", "energy_dram"),
    "gemm-sp": ("flops_sp",),
}
_ORACLE = {
    "memory-read": re.compile(r"total_bytes_read=(\d+)"),
    "memory-write": re.compile(r"total_bytes_written=(\d+)"),
    "gemm-dp": re.compile(r"done total_flops=(\d+)"),
    "gemm-sp": re.compile(r"done total_flops=(\d+)"),
}
_ZONE_RE = re.compile(r"^intel-rapl:\d+(:\d+)?$")
STATUSES = ("PASS", "FAIL", "NOT_RUN", "SKIP")


def result(name, status, ratio=None, reason=""):
    return {"name": name, "status": status, "ratio": ratio, "reason": reason}


def within(family, ratio):
    lo, hi = TOLERANCES[family]
    return ratio is not None and lo <= ratio <= hi


def _limit(family):
    return "limit %g-%g" % TOLERANCES[family]


def _ratio_result(name, family, num, den, detail):
    ratio = float(num) / den
    status = "PASS" if within(family, ratio) else "FAIL"
    return result(name, status, ratio, "%s; ratio %.4f, %s" % (detail, ratio, _limit(family)))



def parse_schema(text):
    """{type: [(key, width, slow)]} from the ``!type key,flags ...`` lines."""
    schema = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("!"):
            continue
        fields = line[1:].split()
        if len(fields) < 2:
            continue
        keys = []
        for tok in fields[1:]:
            parts = tok.split(",")
            width, slow = 64, False
            for opt in parts[1:]:
                if opt.startswith("W=") and opt[2:].isdigit():
                    width = int(opt[2:])
                elif opt == "R=S":
                    slow = True
            keys.append((parts[0], width, slow))
        schema[fields[0]] = keys
    return schema


def parse_sample(text, schema):
    """(header ts, {type: {dev: {key: int}}}); a sentinel ``-n`` stays a negative int."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None, {}
    try:
        ts = float(lines[0].split()[0])
    except ValueError:
        ts = None
    rows = {}
    for line in lines[1:]:
        tok = line.split()
        if len(tok) < 3 or tok[0] not in schema:
            continue
        keys = schema[tok[0]]
        if tok[2] in ("@fast", "@full"):
            values = tok[3:]
            if tok[2] == "@fast":
                keys = [k for k in keys if not k[2]]
        else:
            values = tok[2:]
        row = rows.setdefault(tok[0], {}).setdefault(tok[1], {})
        for (key, _, _), val in zip(keys, values):
            try:
                row[key] = int(val)
            except ValueError:
                continue
    return ts, rows


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def read_snapshot(shm_dir, powercap_root=None):
    """Snapshot of ``fast`` (+ powercap read right after), or None when unreadable."""
    fast = _read(os.path.join(shm_dir, "fast"))
    schema_text = _read(os.path.join(shm_dir, "schema"))
    if fast is None or schema_text is None:
        return None
    schema = parse_schema(schema_text)
    ts, rows = parse_sample(fast, schema)
    if ts is None:
        return None
    return {
        "ts": ts,
        "wall": time.time(),
        "rows": rows,
        "widths": {t: {k: w for k, w, _ in keys} for t, keys in schema.items()},
        "powercap": read_powercap(powercap_root) if powercap_root else None,
    }


def _fast_ts(shm_dir):
    text = _read(os.path.join(shm_dir, "fast"))
    for line in (text or "").splitlines():
        if line.strip():
            try:
                return float(line.split()[0])
            except ValueError:
                return None
    return None


def wait_new_snapshot(shm_dir, wait_max, powercap_root=None, after_ts=None, poll=0.2):
    """First snapshot whose header ts differs from ``after_ts`` (default: the current one)."""
    ref = _fast_ts(shm_dir) if after_ts is None else after_ts
    deadline = time.time() + wait_max
    while time.time() < deadline:
        ts = _fast_ts(shm_dir)
        if ts is not None and ts != ref:
            snap = read_snapshot(shm_dir, powercap_root)
            if snap is not None and snap["ts"] != ref:
                return snap
        time.sleep(poll)
    return None


def read_powercap(root):
    """{zone: {name, energy_uj, max_uj}} for intel-rapl:N[:M] zones; None when none readable."""
    zones = {}
    for path in sorted(glob.glob(os.path.join(root, "intel-rapl:*"))):
        zone = os.path.basename(path)
        if not _ZONE_RE.match(zone):
            continue
        name, uj, mx = (_read(os.path.join(path, f)) for f in
                        ("name", "energy_uj", "max_energy_range_uj"))
        try:
            zones[zone] = {"name": name.strip(), "energy_uj": int(uj), "max_uj": int(mx)}
        except (AttributeError, ValueError):
            continue
    return zones or None



def sum_delta(a, b, type_name, key, skip=()):
    """(sum over rows of the mod-2^W delta, None) or (None, problem); rows in @skip are left out."""
    rows_b = b["rows"].get(type_name)
    if not rows_b:
        return None, "no %s rows" % type_name
    rows_a = a["rows"].get(type_name, {})
    width = b["widths"].get(type_name, {}).get(key, 64)
    total = 0
    for dev in sorted(set(rows_b) - set(skip)):
        vb = rows_b[dev].get(key)
        va = rows_a.get(dev, {}).get(key)
        if vb is None or va is None:
            return None, "%s %s %s missing in the %s snapshot" % (
                type_name, dev, key, "first" if va is None else "second")
        if va < 0 or vb < 0:
            return None, "%s %s %s emitted sentinel %d" % (type_name, dev, key, min(va, vb))
        total += (vb - va) % (1 << width)
    return total, None



def _memory_bytes(a, b, types, kernel):
    """(bytes, notes, None) over every matching row and key, or (None, None, problem)."""
    total, notes, used = 0, [], 0
    structural = (-NOT_BUILT, -NOT_PRESENT)
    for t, src in types:
        rows_a, rows_b = a["rows"].get(t, {}), b["rows"][t]
        for key, scale in src[kernel]:
            if key not in b["widths"].get(t, {}) or all(r.get(key) is None for r in rows_b.values()):
                continue
            skip = {d for d, r in rows_b.items()
                    if r.get(key) in structural and rows_a.get(d, {}).get(key) in structural}
            if skip:
                code = "NOT_BUILT" if any(rows_b[d][key] == -NOT_BUILT for d in skip) else "NOT_PRESENT"
                if len(skip) == len(rows_b):
                    notes.append("%s %s %s, excluded" % (t, key, code))
                    continue
                notes.append("%s %s: %d row(s) %s, excluded" % (t, key, len(skip), code))
            d, problem = sum_delta(a, b, t, key, skip)
            if problem:
                return None, None, problem
            total += d * scale
            used += 1
    if not used:
        return None, None, "no counted %s key (%s)" % (kernel, "; ".join(notes) or "none in schema")
    return total, notes, None


def _scf0_share(a, b, kernel):
    """scf0 bytes / all scf bytes, or None when the payload has no SCF rows."""
    t = "arm_aarch64_imc"
    rows = b["rows"].get(t)
    if not rows or "scf0" not in rows:
        return None
    src = dict(MEMORY_SOURCES)[t]
    total, _, problem = _memory_bytes(a, b, [(t, src)], kernel)
    if problem or not total:
        return None
    s0 = 0
    for key, scale in src[kernel]:
        va = a["rows"].get(t, {}).get("scf0", {}).get(key, 0)
        s0 += ((rows["scf0"].get(key, 0) - va) % (1 << b["widths"][t].get(key, 64))) * scale
    return float(s0) / total


def check_memory(a, b, kernel, oracle_bytes):
    """Memory-controller bytes vs the load's exact bytes; read also yields mem_socket0."""
    name = "mem_" + kernel
    types = [(t, src) for t in sorted(b["rows"]) for prefix, src in MEMORY_SOURCES
             if t.startswith(prefix)]
    if not types:
        main = result(name, "NOT_RUN", reason="no memory-controller type in the fast payload")
    elif oracle_bytes is None or oracle_bytes <= 0:
        main = result(name, "NOT_RUN", reason="load printed no exact byte total")
    else:
        total, notes, problem = _memory_bytes(a, b, types, kernel)
        if problem:
            main = result(name, "NOT_RUN", reason=problem)
        else:
            detail = "daemon %.4g B over %s / load %.4g B" % (
                total, ",".join(t for t, _ in types), oracle_bytes)
            if notes:
                detail += " (" + "; ".join(notes) + ")"
            main = _ratio_result(name, "memory", total, oracle_bytes, detail)
    if kernel != "read":
        return [main]
    share = _scf0_share(a, b, kernel) if main["status"] in ("PASS", "FAIL") else None
    if share is not None:
        status = "PASS" if within("socket0_share", share) else "FAIL"
        s0 = result("mem_socket0", status, share, "scf0 carries %.1f%% of the socket-0 pinned "
                    "read; %s" % (100 * share, _limit("socket0_share")))
    elif main["status"] in ("PASS", "FAIL"):
        s0 = result("mem_socket0", "NOT_RUN",
                    reason="x86 uncore rows sum every socket (no per-socket row)")
    else:
        s0 = result("mem_socket0", "NOT_RUN", reason=main["reason"])
    return [main, s0]


def _weighted(a, b, weights):
    total = 0
    for key, w in sorted(weights.items()):
        d, problem = sum_delta(a, b, "host_cpu_hw", key)
        if problem:
            return None, problem
        total += d * w
    return total, None


def check_flops(a, b, precision, oracle_flops, arch_class):
    name = "flops_" + precision
    if arch_class not in ("intel", "amd", "grace"):
        return result(name, "NOT_RUN",
                      reason="no FP counter mapping for arch class %s" % arch_class)
    if oracle_flops is None or oracle_flops <= 0:
        return result(name, "NOT_RUN", reason="GEMM printed no done total_flops line")
    if arch_class == "grace":
        key = "arm_fp_%s_spec" % precision
        ops, problem = sum_delta(a, b, "host_cpu_hw", key)
        if problem:
            return result(name, "NOT_RUN", reason=problem)
        ceil = GRACE_FLOPS_PER_OP_CEILING[precision]
        if ops == 0:
            return result(name, "FAIL", reason="%s counted 0 under the GEMM" % key)
        fpo = float(oracle_flops) / ops
        status = "PASS" if fpo <= ceil * GRACE_CEILING_SLACK else "FAIL"
        return result(name, status, fpo, "GEMM %.4g FLOPs / %s %.4g ops = %.3f flops per op, "
                      "ceiling %d (above it the count reads low)"
                      % (oracle_flops, key, ops, fpo, ceil))
    if arch_class == "intel":
        flops, problem = _weighted(a, b, INTEL_FP_WEIGHTS[precision])
        how = "fp_arith_inst_retired_*_%s lane-weighted" % (
            "double" if precision == "dp" else "single")
    else:
        total, problem = sum_delta(a, b, "host_cpu_hw", "fp_ops_retired")
        dp, dp_problem = sum_delta(a, b, "host_cpu_hw", "amd_fp_dp_ops")
        if dp_problem:
            flops, how = total, "fp_ops_retired total (no precision split before Zen5)"
        elif precision == "dp":
            flops, how = dp, "amd_fp_dp_ops"
        else:
            flops = None if total is None else total - dp
            how = "fp_ops_retired - amd_fp_dp_ops"
    if problem:
        return result(name, "NOT_RUN", reason=problem)
    return _ratio_result(name, "flops", flops, oracle_flops,
                         "daemon %.4g FLOPs (%s) / GEMM %.4g" % (flops, how, oracle_flops))


def _perf_count(text):
    """(count, None) or (None, problem) for the ``instructions`` line of perf -x, output."""
    for line in text.splitlines():
        f = line.split(",")
        if len(f) >= 3 and "instructions" in f[2]:
            try:
                return float(f[0]), None
            except ValueError:
                return None, "perf instructions: %s" % f[0]
    return None, "perf printed no instructions line"


def check_instructions(a, b, perf):
    name = "instructions"
    if not perf:
        return result(name, "NOT_RUN", reason="perf was not run for this window")
    if perf.get("error"):
        return result(name, "NOT_RUN", reason=perf["error"])
    count, problem = _perf_count(perf.get("output", ""))
    if problem or not count:
        return result(name, "NOT_RUN", reason=problem or "perf counted 0 instructions")
    for key in ("instr_retired", "instr_retired_any"):
        mine, problem = sum_delta(a, b, "host_cpu_hw", key)
        if not problem:
            return _ratio_result(name, "instructions", mine, count,
                                 "daemon %s %.4g / perf -a %.4g" % (key, mine, count))
    return result(name, "NOT_RUN", reason=problem)


def _powercap_delta(a, b, want):
    za, zb = a.get("powercap") or {}, b.get("powercap") or {}
    total, n = 0, 0
    for zone in sorted(zb):
        z = zb[zone]
        if not want(zone, z["name"]) or zone not in za:
            continue
        d = z["energy_uj"] - za[zone]["energy_uj"]
        if z["max_uj"] > 0:
            d %= z["max_uj"] + 1
        elif d < 0:
            return None, "powercap %s went backwards with no max_energy_range_uj" % zone
        total += d
        n += 1
    return (total, None) if n else (None, "no readable powercap zone")


def _package_zone(zone, label):
    return zone.count(":") == 1 and label.startswith("package-")


def _dram_zone(_zone, label):
    return label == "dram"


def check_energy(a, b):
    out = []
    rapl = [t for t in RAPL_TYPES if t in b["rows"]]
    for name, key, want in (("energy_pkg", "pkg_energy", _package_zone),
                            ("energy_dram", "dram_energy", _dram_zone)):
        if not rapl:
            out.append(result(name, "NOT_RUN",
                              reason="no RAPL type in the fast payload (x86 only)"))
            continue
        if key not in b["widths"].get(rapl[0], {}):
            out.append(result(name, "NOT_RUN", reason="%s has no %s key" % (rapl[0], key)))
            continue
        mj, problem = sum_delta(a, b, rapl[0], key)
        if problem:
            out.append(result(name, "NOT_RUN", reason=problem))
            continue
        if not (a.get("powercap") and b.get("powercap")):
            out.append(result(name, "NOT_RUN", reason="powercap energy_uj not readable"))
            continue
        uj, problem = _powercap_delta(a, b, want)
        if problem or not uj:
            out.append(result(name, "NOT_RUN", reason=problem or "powercap delta 0"))
            continue
        out.append(_ratio_result(name, "energy", mj * 1000.0, uj,
                                 "daemon %s %.4g mJ / powercap %.4g uJ" % (key, mj, uj)))
    return out


def oracle_from_log(text, kind):
    found = _ORACLE[kind].findall(text or "")
    return int(found[-1]) if found else None


def evaluate_window(w, kind, arch_class):
    names = WINDOW_CHECKS[kind]
    if w is None:
        return [result(n, "NOT_RUN", reason="window did not finish (killed or timed out)")
                for n in names]
    if w.get("error"):
        return [result(n, w.get("error_status", "NOT_RUN"), reason=w["error"]) for n in names]
    a, b, load = w["a"], w["b"], w.get("load") or {}
    oracle = oracle_from_log(load.get("log_tail", ""), kind)
    if kind.startswith("memory-"):
        out = check_memory(a, b, kind.split("-")[1], oracle)
    else:
        out = [check_flops(a, b, kind.split("-")[1], oracle, arch_class)]
        if kind == "gemm-dp":
            out.append(check_instructions(a, b, w.get("perf")))
            out.extend(check_energy(a, b))
    if oracle is None:
        last = (load.get("log_tail", "").strip().splitlines() or ["none"])[-1][:200]
        why = "load exit %s%s; last line: %s" % (
            load.get("rc"), " (timed out)" if load.get("timed_out") else "", last)
        for r in out:
            if r["status"] == "NOT_RUN" and "printed no" in r["reason"]:
                r["reason"] += " (%s)" % why
    return out



def _child_setup():
    os.setsid()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


def _spawn(cmd, out):
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                            preexec_fn=_child_setup)


def _stop(proc, sig=signal.SIGTERM, grace=5):
    """Signal the child's process group, then SIGKILL it after ``grace`` seconds."""
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, sig)
        proc.wait(timeout=grace)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.wait()


def run_window(shm, out, cmd, wait_max, load_timeout, log, perf=None, powercap=None):
    """Snapshot, run ``cmd`` (bounded; perf -a alongside), snapshot after it; JSON to ``out``."""
    w = {"a": None, "b": None, "load": None, "perf": None, "error": None}
    procs = []

    def _term(signum, _frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    perf_out = out + ".perf"
    try:
        w["a"] = wait_new_snapshot(shm, wait_max, powercap)
        if w["a"] is None:
            w.update(error="fast payload did not advance within %g s" % wait_max,
                     error_status="FAIL")
            return 3
        perf_proc = None
        if perf:
            exe = shutil.which(perf)
            if exe is None:
                w["perf"] = {"output": "", "error": "perf not found (%s)" % perf}
            else:
                perf_proc = _spawn([exe, "stat", "-a", "-x,", "-o", perf_out, "-e", "instructions"],
                                   subprocess.DEVNULL)
                procs.append(perf_proc)
        with open(log, "w") as logf:
            try:
                load = _spawn(cmd, logf)
            except OSError as exc:
                logf.write("cannot run %s: %s\n" % (cmd[0], exc))
                w["load"] = {"rc": 127, "timed_out": False}
            else:
                procs.append(load)
                try:
                    w["load"] = {"rc": load.wait(timeout=load_timeout), "timed_out": False}
                except subprocess.TimeoutExpired:
                    _stop(load)
                    w["load"] = {"rc": load.returncode, "timed_out": True}
        w["load"]["log_tail"] = (_read(log) or "")[-8192:]
        w["b"] = wait_new_snapshot(shm, wait_max, powercap, after_ts=_fast_ts(shm))
        if perf_proc is not None:
            _stop(perf_proc, signal.SIGINT, grace=15)
            w["perf"] = {"output": _read(perf_out) or "", "rc": perf_proc.returncode,
                         "error": None}
        if w["b"] is None:
            w.update(error="fast payload did not advance within %g s after the load" % wait_max,
                     error_status="FAIL")
            return 3
        return 0
    except SystemExit:
        w.update(error="interrupted", error_status="NOT_RUN")
        raise
    finally:
        for p in procs:
            _stop(p)
        with open(out, "w") as f:
            json.dump(w, f)



def _field(text, name):
    m = re.search(r"^%s\s*:\s*(.*)$" % re.escape(name), text, re.M)
    return m.group(1).strip() if m else ""


def arch_info(cpuinfo="/proc/cpuinfo", pmu_name="/sys/bus/event_source/devices/cpu/caps/pmu_name"):
    """{class, vendor, family, model, pmu_name}; class is intel, amd, grace or other."""
    text = _read(cpuinfo) or ""
    pmu = (_read(pmu_name) or "").strip() or "none"
    vendor = _field(text, "vendor_id")
    if vendor:
        cls = {"GenuineIntel": "intel", "AuthenticAMD": "amd"}.get(vendor, "other")
        return {"class": cls, "vendor": vendor, "family": _field(text, "cpu family"),
                "model": _field(text, "model"), "pmu_name": pmu}
    impl, part = _field(text, "CPU implementer"), _field(text, "CPU part")
    if impl:
        cls = "grace" if (impl.lower(), part.lower()) == ("0x41", "0xd4f") else "other"
        return {"class": cls, "vendor": "ARM", "family": impl, "model": part, "pmu_name": pmu}
    return {"class": "other", "vendor": "unknown", "family": "", "model": "", "pmu_name": pmu}


def socket0_cpus(sys_cpu="/sys/devices/system/cpu"):
    """(cpulist, count) of the online CPUs of physical package 0."""
    found = []
    for path in glob.glob(os.path.join(sys_cpu, "cpu[0-9]*")):
        cpu = os.path.basename(path)[3:]
        if not cpu.isdigit():
            continue
        if (_read(os.path.join(path, "online")) or "1").strip() != "1":
            continue
        if (_read(os.path.join(path, "topology", "physical_package_id")) or "").strip() == "0":
            found.append(int(cpu))
    found.sort()
    spans = []
    for c in found:
        if spans and c == spans[-1][1] + 1:
            spans[-1][1] = c
        else:
            spans.append([c, c])
    return ",".join(str(s) if s == e else "%d-%d" % (s, e) for s, e in spans), len(found)



def append_results(path, results):
    with open(path, "a") as f:
        for r in results:
            ratio = "-" if r["ratio"] is None else "%.4f" % r["ratio"]
            reason = " ".join(str(r["reason"]).split())
            f.write("%s\t%s\t%s\t%s\n" % (r["name"], r["status"], ratio, reason))


def load_results(path):
    """{name: result} from a results TSV (last line per name wins)."""
    got = {}
    for line in (_read(path) or "").splitlines():
        f = line.split("\t")
        if len(f) < 2 or f[1] not in STATUSES:
            continue
        try:
            ratio = float(f[2]) if len(f) > 2 and f[2] not in ("", "-") else None
        except ValueError:
            ratio = None
        got[f[0]] = result(f[0], f[1], ratio, f[3] if len(f) > 3 else "")
    return got


def _print(r):
    ratio = "-" if r["ratio"] is None else "%.4f" % r["ratio"]
    print("counter_accuracy %-12s %-7s ratio=%s %s" % (r["name"], r["status"], ratio, r["reason"]))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("arch")
    s.add_argument("--cpuinfo", default="/proc/cpuinfo")
    s.add_argument("--pmu-name", default="/sys/bus/event_source/devices/cpu/caps/pmu_name")
    s.add_argument("--field")
    s = sub.add_parser("socket0-cpus")
    s.add_argument("--sys-cpu", default="/sys/devices/system/cpu")
    s = sub.add_parser("window")
    s.add_argument("--shm", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--wait-max", type=float, required=True)
    s.add_argument("--load-timeout", type=float, required=True)
    s.add_argument("--log", required=True)
    s.add_argument("--perf")
    s.add_argument("--powercap")
    s.add_argument("command", nargs=argparse.REMAINDER)
    s = sub.add_parser("evaluate")
    s.add_argument("--window", required=True)
    s.add_argument("--kind", required=True, choices=sorted(WINDOW_CHECKS))
    s.add_argument("--arch", required=True)
    s.add_argument("--results", required=True)
    s = sub.add_parser("record")
    s.add_argument("--results", required=True)
    s.add_argument("--status", required=True, choices=STATUSES)
    s.add_argument("--reason", required=True)
    s.add_argument("--all", action="store_true")
    s.add_argument("names", nargs="*")
    s = sub.add_parser("summarize")
    s.add_argument("--results", required=True)
    args = p.parse_args(argv)

    if args.cmd == "arch":
        info = arch_info(args.cpuinfo, args.pmu_name)
        print(info[args.field] if args.field else json.dumps(info, sort_keys=True))
        return 0
    if args.cmd == "socket0-cpus":
        print("%s %d" % socket0_cpus(args.sys_cpu))
        return 0
    if args.cmd == "window":
        cmd = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not cmd:
            p.error("window needs -- CMD")
        return run_window(args.shm, args.out, cmd, args.wait_max, args.load_timeout, args.log,
                          args.perf, args.powercap)
    if args.cmd == "evaluate":
        text = _read(args.window)
        try:
            w = json.loads(text) if text else None
        except ValueError:
            w = None
        res = evaluate_window(w, args.kind, args.arch)
        for r in res:
            _print(r)
        append_results(args.results, res)
        return 0
    if args.cmd == "record":
        names = list(COUNTER_CHECKS) if args.all else args.names
        append_results(args.results, [result(n, args.status, reason=args.reason) for n in names])
        return 0
    if args.cmd == "summarize":
        got = load_results(args.results)
        bad = False
        for n in COUNTER_CHECKS:
            r = got.get(n, result(n, "NOT_RUN", reason="no result (stopped early)"))
            _print(r)
            bad = bad or r["status"] == "FAIL"
        print("RESULT: FAIL" if bad else "RESULT: PASS")
        return 1 if bad else 0
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
