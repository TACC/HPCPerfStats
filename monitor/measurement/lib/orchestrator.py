"""Whole-machine overhead profile orchestrator (driven by ``bin/run_overhead_profile.sh``).

Runs the release RPM as the real ``hpcperfstats.service`` and walks the bracketed block
schedule (OFF / NONE / ALL / SOLO(driver) …). Collector sets come only from a runtime drop-in
under /run (gone on reboot, removed by the restore path):

  ALL, OFF        no drop-in: production ExecStart, production queue, full payloads
  NONE, SOLO(D)   Environment=HPCPERFSTATS_DISABLE_TYPES=<csv> and ExecStart pointed at
                  MEASURE_CONF, a copy of the site conf whose only change is
                  ``queue <queue>_measure`` (CADENCE legs also change ``sample_freq``)
  VARIANT(V)      as SOLO(base) with one measurement-only daemon knob (driver_map.VARIANTS,
                  e.g. HPCPERFSTATS_LIKWID_GRACE_SCF=0); paired with SOLO(base) per block

Governor leg sets (D13, D25): ``--governors performance`` (default) runs one block schedule with
every CPU pinned to ``performance``; a comma list (e.g. ``performance,dynamic``) repeats the
whole schedule once per governor; before each set every CPU's ``scaling_governor`` is written
(``dynamic`` resolves per cpufreq driver, lib/cpufreq.py) and the cleanup path writes every
CPU's original governor back. A governor the driver does not offer fails preflight before any
change. Preflight also refuses a node whose CPUs are busy outside the daemon (> 1 core) unless
``--allow-busy-node``.

After every leg that publishes to ``<queue>_measure`` the queue is drained through the
management API and the last ``$`` schema message is kept as ``raw/legs/<id>/schema.txt``, so
the run records each type's emitted keys (lib/schema_capture.py).

Why a conf copy and not ``-q``: the daemon parses the CLI first and the conf file second
(monitor.c main), so the conf ``queue`` key would override ``-q`` and partial payloads would
reach the production queue. A test pins that ordering.

The site conf is never edited in place: it is saved before ``rpm -Uvh`` (the spec lists it
without %config, so the RPM overwrites it), written back right after, and restored on exit.
``restore.sh`` with the same steps is written into the result dir before any change.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import (
    cpufreq,
    driver_map,
    fwq,
    journal_types,
    profile_report,
    provenance,
    rmq_cleanup,
    schedule,
    schema_capture,
)
from .nodesample import parse_cpu_list, parse_proc_stat
from .perfstat import DEFAULT_EVENTS
from .procsample import CLK_TCK, cpu_delta, sample_process

HARNESS_VERSION = "1.0.0"
UNIT = "hpcperfstats"
SITE_CONF = "/etc/hpcperfstats/hpcperfstats.conf"
SYSCONFIG = "/etc/sysconfig/hpcperfstatsd"
DAEMON = "/usr/sbin/hpcperfstatsd"
DROPIN_DIR = "/run/systemd/system/hpcperfstats.service.d"
DROPIN_PATH = DROPIN_DIR + "/90-measure.conf"
MEASURE_DIR = "/run/hpcperfstats-measure"
MEASURE_CONF = MEASURE_DIR + "/hpcperfstats.conf"
MEASUREMENT_ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = MEASUREMENT_ROOT.parents[1]
# Legs whose payloads go to <queue>_measure (drained for the schema after each one).
MEASURE_QUEUE_KINDS = ("NONE", "SOLO", "VARIANT", "CADENCE")
FORBIDDEN_SYSCONFIG = ("HPCPERFSTATS_DISABLE_TYPES", "HPCPERFSTATS_COLLECTION_PROFILE")
UNIT_PROPS = ("ExecStart", "Nice", "IOSchedulingClass", "IOSchedulingPriority", "Environment",
              "EnvironmentFiles", "CPUAffinity", "LimitNOFILE", "ProtectSystem", "Restart",
              "DropInPaths", "MainPID")
# --profile-collectors: an --enable-metric-profiler build of the same sources, run for one
# ALL-configuration leg; its metric-profiler: report (cumulative, emitted at shutdown) ranks
# collector types. Instrumented build: never a headline number.
PROFILER_BINARY = MEASURE_DIR + "/hpcperfstatsd-profiler"
DISCOVERY_SECONDS = 120
TYPES_WAIT_SECONDS = 120
SYSFS_CPU = cpufreq.SYSFS_CPU
PROC_ROOT = "/proc"
# CoreSight uncore PMUs (Grace SCF via nvidia_cspmu); an unbound one silently drops a collector.
SYS_PLATFORM_DEVICES = "/sys/bus/platform/devices"
CS_PMU_PREFIX = "arm-cs-arch-pmu."
# Preflight busy-node probe: node busy cores minus the daemon's own CPU over this window.
BUSY_PROBE_SECONDS = 5
BUSY_NODE_CORES = 1.0


class PreflightError(RuntimeError):
    """A condition that makes the run invalid; never overridden by --force."""


# ----------------------------------------------------------------------------- pure helpers
def parse_conf(text: str) -> dict[str, str]:
    """Key/value view of an hpcperfstats.conf (separators as the daemon: space, :, tab, =)."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"([^\s:=]+)[\s:=]+(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def check_site_conf(text: str) -> list[str]:
    conf = parse_conf(text)
    errs = []
    for key in ("queue", "server"):
        if not conf.get(key):
            errs.append(f"conf has no `{key}` key")
    if conf.get("disable_types"):
        errs.append(f"site conf sets disable_types {conf['disable_types']!r}: ALL must be all "
                    "collectors; remove it for the measurement")
    prof = conf.get("collection_profile")
    if prof and prof != "default":
        errs.append(f"site conf sets collection_profile {prof!r}: only `default` is allowed")
    return errs


def check_sysconfig(text: str) -> list[str]:
    errs = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("\"'")
        if key in FORBIDDEN_SYSCONFIG and val and not (
                key.endswith("COLLECTION_PROFILE") and val == "default"):
            errs.append(f"{SYSCONFIG} sets {key}={val}: EnvironmentFile would override the "
                        "measurement drop-in; comment it out")
    return errs


def is_packaged_default_conf(text: str) -> bool:
    conf = parse_conf(text)
    return (conf.get("server") == "localhost" and conf.get("user") == "guest"
            and conf.get("password") == "guest")


def measure_conf_text(site_text: str, sample_freq: float | None = None) -> str:
    conf = parse_conf(site_text)
    queue = conf.get("queue")
    if not queue or queue.endswith(rmq_cleanup.MEASURE_SUFFIX):
        raise PreflightError(f"site queue {queue!r} is missing or already ends in "
                             f"{rmq_cleanup.MEASURE_SUFFIX}")
    lines = []
    for raw in site_text.splitlines():
        key = parse_conf(raw)
        if "queue" in key:
            continue
        if sample_freq is not None and "sample_freq" in key:
            continue
        if sample_freq is not None and "sample_freq_slow" in key:
            slow = float(key["sample_freq_slow"])
            if slow < sample_freq:
                continue
        lines.append(raw)
    lines.append(f"queue {rmq_cleanup.measure_queue_name(queue)}")
    if sample_freq is not None:
        lines.append(f"sample_freq {sample_freq:g}")
        slow = float(conf.get("sample_freq_slow") or 0)
        if slow < sample_freq:
            lines.append(f"sample_freq_slow {sample_freq:g}")
    return "\n".join(lines) + "\n"


def dropin_text(kind: str, active: list[str], groups: dict[str, list[str]],
                group: str | None = None, binary: str = DAEMON,
                env: Mapping[str, str] | None = None) -> str | None:
    if kind in ("ALL", "OFF"):
        return None
    body = ["[Service]"]
    csv = driver_map.disable_csv(kind, active, groups, group)
    if csv is not None:
        body.append(f"Environment=HPCPERFSTATS_DISABLE_TYPES={csv}")
    # VARIANT legs: measurement-only daemon knobs (driver_map.VARIANTS), same runtime drop-in.
    body += [f"Environment={k}={v}" for k, v in sorted((env or {}).items())]
    # PROFILE runs every collector with full payloads, so it keeps the production conf/queue.
    conf = SITE_CONF if kind == "PROFILE" else MEASURE_CONF
    body += ["ExecStart=", f"ExecStart={binary} -c {conf}"]
    return "\n".join(body) + "\n"


def check_slurm_jobs(squeue_users: str, invoking_user: str | None) -> list[str]:
    return sorted({u.strip() for u in squeue_users.splitlines() if u.strip()} - {invoking_user})


def invoking_user(env: Mapping[str, str], loginuid: Path = Path("/proc/self/loginuid")) -> str | None:
    """Return the login that reserved the node, even from a root shell.

    ``USER`` is ``root`` after ``su -`` or a root login, so it is the last resort. The sudo
    caller, the Slurm job owner, then the audit login uid (kept across ``su``; 0 and the unset
    value 4294967295 are skipped) come first.

    Args:
        env: Process environment (``os.environ`` in production).
        loginuid: Audit login uid file.

    Returns:
        The user name, or None when nothing names one.
    """
    if env.get("SUDO_USER") or env.get("SLURM_JOB_USER"):
        return env.get("SUDO_USER") or env.get("SLURM_JOB_USER")
    try:
        uid = int(loginuid.read_text().strip())
        if 0 < uid < 4294967295:
            return pwd.getpwuid(uid).pw_name
    except (OSError, ValueError, KeyError):
        pass
    return env.get("USER")


def check_load(load1: float, nproc: int) -> str | None:
    limit = 0.1 * nproc
    return None if load1 < limit else f"load {load1:.2f} >= {limit:.2f} (0.1 x nproc)"


def idle_flags(forced: bool, problems: list[str]) -> dict[str, Any]:
    if not problems:
        return {}
    if not forced:
        raise PreflightError("node is not idle: " + "; ".join(problems) + " (use --force for a "
                             "smoke run; results are flagged not_idle and never cited)")
    return {"not_idle": "smoke only", "not_idle_reasons": list(problems)}


def check_busy_node(busy: float | None, allow: bool) -> dict[str, Any]:
    """Refuse a node busy outside the daemon (> BUSY_NODE_CORES) unless ``--allow-busy-node``.

    Args:
        busy: Node busy cores minus the daemon's CPU over the probe window (None: unreadable).
        allow: ``--allow-busy-node`` was given.

    Returns:
        Flags for run.json (``busy_node`` when allowed through, else empty).

    Raises:
        PreflightError: busy and not allowed.
    """
    if busy is None or busy <= BUSY_NODE_CORES:
        return {}
    if not allow:
        raise PreflightError(f"node busy: {busy:.2f} cores in use outside the daemon (limit "
                             f"{BUSY_NODE_CORES:g}); free the node or pass --allow-busy-node")
    return {"busy_node": {"busy_cores": busy, "limit": BUSY_NODE_CORES}}


def check_cs_pmus(devices_dir: str, allow: bool) -> dict[str, Any]:
    """Refuse a node whose CoreSight uncore PMU devices are not bound to a driver.

    c642-041 2026-11-09: firmware APMT listed 5 ``arm-cs-arch-pmu.N`` devices, the driver
    rejected them (``No cpu associated with the PMU``), no ``nvidia_scf_pmu_*`` existed, and
    ``arm_aarch64_imc`` disabled itself, so that node's legs lacked a collector the others had.

    Args:
        devices_dir: Platform device directory (``/sys/bus/platform/devices``).
        allow: ``--allow-unbound-pmu`` was given.

    Returns:
        Flags for run.json (``unbound_pmu`` when allowed through, else empty).

    Raises:
        PreflightError: an unbound PMU device and not allowed.
    """
    root = Path(devices_dir)
    devs = sorted(root.glob(CS_PMU_PREFIX + "*")) if root.is_dir() else []
    unbound = [d.name for d in devs if not (d / "driver").exists()]
    if not unbound:
        return {}
    if not allow:
        raise PreflightError(f"{len(unbound)} of {len(devs)} CoreSight PMU devices have no driver "
                             f"({', '.join(unbound)}); uncore collectors would be missing. Check "
                             "`dmesg | grep arm-cs`; reboot or pass --allow-unbound-pmu")
    return {"unbound_pmu": {"unbound": unbound, "devices": len(devs)}}


def restore_script_text(saved_conf: Path | None, was_active: bool, was_enabled: bool,
                        measure_queue: str, mgmt_hint: str | None = None,
                        governor_lines: list[str] | None = None) -> str:
    lines = ["#!/usr/bin/env bash", "# Restore written by run_overhead_profile.sh before any change.",
             "# The measured package stays installed (reserved node, plan decision 1).",
             "set -u", f"rm -f {DROPIN_PATH}", f"rmdir {DROPIN_DIR} 2>/dev/null",
             f"rm -rf {MEASURE_DIR}", "systemctl daemon-reload"]
    if governor_lines:
        lines += ["# Original cpufreq governors:", *governor_lines]
    if saved_conf is not None:
        lines.append(f"install -m 0600 {saved_conf} {SITE_CONF}")
    lines.append(f"systemctl {'enable' if was_enabled else 'disable'} {UNIT}")
    lines.append(f"systemctl {'restart' if was_active else 'stop'} {UNIT}")
    lines.append(f"# Measurement queue: delete {measure_queue} if it is still on the broker:")
    lines.append(f"#   {mgmt_hint or rmq_cleanup.admin_command(measure_queue)}")
    return "\n".join(lines) + "\n"


def _rmdir_if_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass  # absent, or holds another drop-in we did not write


def default_fwq_cpus(online: list[int], harness_cpu: int) -> list[int]:
    return [c for c in online if c != harness_cpu] or online


def fmt_cpus(cpus: list[int]) -> str:
    return ",".join(str(c) for c in cpus)


# ----------------------------------------------------------------------------- argument parsing
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_overhead_profile.sh",
        description="Whole-machine overhead profile of the release hpcperfstatsd RPM.")
    p.add_argument("--rpm", required=True, help="release hpcperfstatsd RPM to install and measure")
    p.add_argument("--build-info", type=Path, help="rpmbuild/build-info dir with capabilities json")
    p.add_argument("--conf", type=Path, help="site conf to install (fresh installs, trial runs)")
    p.add_argument("--out", type=Path, help="result directory (default: results/<host>_<arch>_...)")
    p.add_argument("--leg-seconds", type=int, default=1800)
    p.add_argument("--settle-seconds", type=int, default=60)
    p.add_argument("--fwq-seconds", type=int, default=300)
    p.add_argument("--fwq-cpus", help="CPU list for FWQ (default: all online but the harness CPU)")
    p.add_argument("--fwq-quantum-us", type=int, default=1000)
    p.add_argument("--blocks", type=int, default=2)
    p.add_argument("--max-hours", type=float, default=12.0)
    p.add_argument("--seed", type=int, help="SOLO shuffle seed (default: time-based, recorded)")
    p.add_argument("--no-off-legs", action="store_true")
    p.add_argument("--cadence-sweep", help="comma list of sample_freq seconds for extra ALL legs")
    p.add_argument("--profile-collectors", action="store_true",
                   help="add one instrumented leg (--enable-metric-profiler build); diagnostic only")
    p.add_argument("--profiler-binary", type=Path,
                   help="prebuilt --enable-metric-profiler hpcperfstatsd (default: build it from "
                        "<build-info>/../SOURCES/hpcperfstats-*.tar.gz)")
    p.add_argument("--cyclictest", action="store_true", help="also run cyclictest on headline legs")
    p.add_argument("--harness-cpu", type=int, help="CPU for the sampler (default: last online)")
    p.add_argument("--amqp-port", type=int, help="broker port (default: conf `port` or 5672)")
    p.add_argument("--rmq-mgmt-port", type=int, default=rmq_cleanup.DEFAULT_MGMT_PORT)
    p.add_argument("--governors", default=cpufreq.DEFAULT_GOVERNORS,
                   help="comma list; one full leg set per governor (default performance; "
                        "dynamic = ondemand on cppc_cpufreq/acpi-cpufreq, powersave on "
                        "intel_pstate/amd-pstate-epp; `none` leaves the governor alone)")
    p.add_argument("--allow-fit", action="store_true",
                   help="let the harness shorten legs / drop legs to fit --max-hours (default: "
                        "refuse, so every leg keeps --leg-seconds and runs compare per leg)")
    p.add_argument("--allow-unbound-pmu", action="store_true",
                   help="run although CoreSight uncore PMU devices are unbound (flagged "
                        "unbound_pmu; that node misses uncore collectors)")
    p.add_argument("--allow-busy-node", action="store_true",
                   help="run although > 1 core is busy outside the daemon (flagged busy_node)")
    p.add_argument("--force", action="store_true",
                   help="run on a non-idle node; results are flagged `not_idle: smoke only`")
    p.add_argument("--allocation-user",
                   help="owner of this node's Slurm reservation (default: SUDO_USER, "
                        "SLURM_JOB_USER, audit login uid, then USER)")
    p.add_argument("--dry-run", action="store_true", help="print the plan; change nothing")
    p.add_argument("--dry-run-types", help="dry run: comma list standing in for Enabled types")
    return p


def _schedule(args: argparse.Namespace, groups: dict[str, list[str]], seed: int,
              caps: dict[str, Any] | None, governors: list[str] | None = None) -> dict[str, Any]:
    sweep = [float(x) for x in args.cadence_sweep.split(",")] if args.cadence_sweep else []
    variants = driver_map.variants_for(groups, caps)
    plan = schedule.build_schedule(
        list(groups), blocks=args.blocks, seed=seed, leg_seconds=args.leg_seconds,
        settle_seconds=args.settle_seconds, fwq_seconds=args.fwq_seconds,
        max_hours=args.max_hours, off_legs=not args.no_off_legs, cadence_sweep=sweep,
        profile_collectors=args.profile_collectors, variants=variants, governors=governors or [],
        allow_fit=args.allow_fit)
    plan["variant_specs"] = variants
    return plan


def leg_groups(groups: dict[str, list[str]], plan: dict[str, Any]) -> dict[str, list[str]]:
    """Driver groups plus each scheduled variant's type list (for disable_csv on any leg)."""
    return {**groups, **{n: v["types"] for n, v in (plan.get("variant_specs") or {}).items()}}


def _load_caps(build_info: Path | None) -> dict[str, Any] | None:
    if build_info and (build_info / "monitor-build-capabilities.json").is_file():
        return json.loads((build_info / "monitor-build-capabilities.json").read_text())
    return None


def _leg_line(i: int, leg: dict[str, Any], plan: dict[str, Any], active: list[str],
              groups: dict[str, list[str]]) -> str:
    dur = plan["leg_seconds"] + plan["settle_seconds"] + (plan["fwq_seconds"] if leg["fwq"] else 0)
    label = f"[{leg['governor']}] " if leg.get("governor") else ""
    label += leg["kind"]
    if leg["kind"] in ("SOLO", "VARIANT"):
        label += f" {leg['group']}"
    if leg["kind"] == "CADENCE":
        label += f" sample_freq={leg['sample_freq']:g}"
    csv = driver_map.disable_csv(leg["kind"], active, leg_groups(groups, plan), leg.get("group"))
    extra = f"disable={csv or '(none)'}" if csv is not None else "production ExecStart"
    if leg.get("env"):
        extra += " " + " ".join(f"{k}={v}" for k, v in sorted(leg["env"].items()))
    if leg["kind"] == "OFF":
        extra = "service stopped"
    if leg["kind"] == "PROFILE":
        extra = "instrumented build (--enable-metric-profiler), not a headline number"
    return (f"  {i:3d}  {label:<40} block {leg['block']}  {dur // 60:4d} min  "
            f"{'FWQ ' if leg['fwq'] else '    '}{extra}")


def print_plan(args: argparse.Namespace, active: list[str], groups: dict[str, list[str]],
               plan: dict[str, Any], out: Callable[[str], None] = print) -> None:
    out("hpcperfstatsd overhead profile — DRY RUN (nothing is changed)")
    out(f"RPM: {args.rpm}")
    out(f"Install: rpm -Uvh --replacepkgs {args.rpm}  (site conf saved first, restored right after)")
    out("Active types: " + ",".join(active))
    out("Driver groups: " + "; ".join(f"{g}: {','.join(t)}" for g, t in groups.items()))
    for name, v in (plan.get("variant_specs") or {}).items():
        out(f"Variant {name}: {','.join(v['types'])} vs SOLO {v['base']} ({v['label']})")
    govs = plan.get("governors") or []
    out("Governor leg sets: " + (", ".join(govs) + " (scaling_governor written for every CPU "
                                 "before each set; originals restored on exit)" if govs
                                 else "none (governor left as is)"))
    steps = ", ".join(f"{s['step']}" for s in plan["fit_steps"]) or "none"
    out(f"Schedule (seed {plan['seed']}, blocks {plan['blocks']}, leg {plan['leg_seconds']} s, "
        f"settle {plan['settle_seconds']} s, fwq {plan['fwq_seconds']} s, fit steps: {steps}):")
    for i, leg in enumerate(plan["legs"], 1):
        out(_leg_line(i, leg, plan, active, groups))
    hours = plan["projected_seconds"] / 3600
    # dt.timezone.utc, not dt.UTC: nodes run python3.9.
    now = dt.datetime.now(dt.timezone.utc)  # noqa: UP017
    end = now + dt.timedelta(seconds=plan["projected_seconds"])
    out(f"Projected duration: {hours:.2f} h (includes {plan['setup_seconds'] / 3600:.2f} h build/"
        f"install/discovery) of --max-hours {plan['max_hours']:g}")
    out(f"Projected end: {end.strftime('%Y-%m-%dT%H:%MZ')}")
    out("Per leg: write or remove " + DROPIN_PATH + "; systemctl daemon-reload; "
        "systemctl restart|stop hpcperfstats; 1 Hz sampler (pinned); perf stat -p PID (ON legs); "
        "FWQ on headline legs; summarize_profile.py")
    out("Restore trap: remove the drop-in and " + MEASURE_DIR + ", daemon-reload, delete "
        "<queue>_measure via the management API, restore the site conf, the service's "
        "active/enabled state and every CPU's original governor. The measured package stays "
        "installed.")


# ----------------------------------------------------------------------------- real run
class Host:
    """Command runner that logs every command, exit status and duration to raw/commands.log."""

    def __init__(self, log: Path):
        self.log = log

    def run(self, cmd: list[str], check: bool = True, timeout: float | None = 600,
            input_text: str | None = None) -> subprocess.CompletedProcess:
        t0 = time.time()
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                 input=input_text, check=False)
        except subprocess.TimeoutExpired:
            self._log(cmd, "timeout", time.time() - t0)
            if check:
                raise
            return subprocess.CompletedProcess(cmd, 124, "", "timeout")
        self._log(cmd, res.returncode, time.time() - t0)
        if check and res.returncode != 0:
            raise RuntimeError(f"command failed ({res.returncode}): {' '.join(cmd)}\n{res.stderr}")
        return res

    def _log(self, cmd: list[str], rc: Any, secs: float) -> None:
        with self.log.open("a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} rc={rc} "
                     f"{secs:.1f}s {' '.join(cmd)}\n")


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(path, 0o600)


class Run:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.rpm = Path(args.rpm).resolve()
        self.started = time.time()
        self.cleanup_done = False
        self.state: dict[str, Any] = {}
        self.governors: list[str] = []
        self.saved_governors: dict[int, str | None] = {}
        self.current_governor: str | None = None

    # -- small systemd/journal helpers
    def systemctl(self, *argv: str, check: bool = True) -> subprocess.CompletedProcess:
        return self.host.run(["systemctl", *argv], check=check)

    def main_pid(self) -> int | None:
        res = self.systemctl("show", "-p", "MainPID", "--value", UNIT, check=False)
        try:
            pid = int(res.stdout.strip() or 0)
        except ValueError:
            return None
        return pid or None

    def journal_since(self, since: float) -> str:
        res = self.host.run(["journalctl", "-u", UNIT, "--since", f"@{int(since)}", "-o",
                             "short-unix", "--no-pager"], check=False)
        return res.stdout

    def wait_enabled_types(self, since: float) -> tuple[list[str] | None, float | None, str]:
        deadline = time.time() + TYPES_WAIT_SECONDS
        text = ""
        while time.time() < deadline:
            text = self.journal_since(since)
            for line in reversed(text.splitlines()):
                if "Enabled types:" in line:
                    ts = None
                    head = line.split(" ", 1)[0]
                    try:
                        ts = float(head) - since
                    except ValueError:
                        pass
                    return journal_types.last_enabled_types(line), ts, text
            time.sleep(1)
        return None, None, text

    # -- preflight / install
    def preflight(self) -> None:
        a = self.args
        # Governors first: an unsupported one fails before anything (governor included) changes.
        try:
            gplan = cpufreq.plan_governors(a.governors, SYSFS_CPU)
        except cpufreq.GovernorError as err:
            raise PreflightError(str(err)) from None
        self.governors = gplan["governors"]
        self.saved_governors = gplan["state"]["governors"] if self.governors else {}
        self.state["cpufreq"] = {"driver": gplan["state"]["driver"],
                                 "available": gplan["state"]["available"],
                                 "original": gplan["state"]["governors"],
                                 "requested": a.governors, "governors": self.governors}
        if a.conf is None and not Path(SITE_CONF).is_file():
            raise PreflightError(f"no site conf at {SITE_CONF} (fresh install); pass --conf PATH "
                                 "with the site server/queue")
        conf_path = a.conf or Path(SITE_CONF)
        self.conf_text = Path(conf_path).read_text()
        if a.conf is None and is_packaged_default_conf(self.conf_text):
            raise PreflightError(f"{SITE_CONF} is the packaged localhost/guest conf; pass --conf")
        errs = check_site_conf(self.conf_text)
        if Path(SYSCONFIG).is_file():
            errs += check_sysconfig(Path(SYSCONFIG).read_text())
        if errs:
            raise PreflightError("; ".join(errs))
        self.conf = parse_conf(self.conf_text)
        self.amqp_port = a.amqp_port or int(self.conf.get("port") or 5672)
        self.measure_queue = rmq_cleanup.measure_queue_name(self.conf["queue"])
        mq = rmq_cleanup.measure_queue_preflight(self.conf, mgmt_port=a.rmq_mgmt_port)
        if not mq["ok"]:
            raise PreflightError(mq["reason"])
        self.state["measure_queue_preflight"] = mq
        problems = []
        if shutil.which("squeue"):
            short = os.uname().nodename.split(".")[0]
            res = self.host.run(["squeue", "-h", "-w", short, "-o", "%u"], check=False)
            owner = self.state["allocation_user"] = a.allocation_user or invoking_user(os.environ)
            others = check_slurm_jobs(res.stdout, owner)
            if others:
                problems.append(f"Slurm jobs of other users on this node: {','.join(others)} (you are "
                                f"{owner}; pass --allocation-user NAME if the reservation is yours)")
        load1 = float(Path("/proc/loadavg").read_text().split()[0])
        msg = check_load(load1, os.cpu_count() or 1)
        if msg:
            problems.append(msg)
        self.flags = idle_flags(a.force, problems)
        busy = self.probe_busy_cores()
        self.state["preflight_busy_cores"] = busy
        self.flags.update(check_busy_node(busy, a.allow_busy_node))
        self.flags.update(check_cs_pmus(SYS_PLATFORM_DEVICES, a.allow_unbound_pmu))
        if a.no_off_legs:
            self.flags["off_legs_skipped"] = True
        free = shutil.disk_usage(self.out).free
        need = 2 * 1024 ** 3
        if free < need:
            raise PreflightError(f"only {free / 1024 ** 3:.1f} GiB free under {self.out}; need 2 GiB")
        self.online = parse_cpu_list(Path("/sys/devices/system/cpu/online").read_text())
        self.harness_cpu = a.harness_cpu if a.harness_cpu is not None else self.online[-1]
        self.fwq_cpus = (parse_cpu_list(a.fwq_cpus) if a.fwq_cpus
                         else default_fwq_cpus(self.online, self.harness_cpu))
        self.perf_events = self.probe_perf()

    def probe_busy_cores(self) -> float | None:
        """Node busy cores minus the daemon's own CPU over BUSY_PROBE_SECONDS (OFF-state load)."""
        def snap() -> tuple[float, dict[str, int], dict[str, Any] | None]:
            stat = Path(PROC_ROOT, "stat").read_text()
            pid = self.main_pid()
            return time.time(), parse_proc_stat(stat)["cpu"], (
                sample_process(pid, PROC_ROOT) if pid else None)
        t0, c0, p0 = snap()
        time.sleep(BUSY_PROBE_SECONDS)
        t1, c1, p1 = snap()
        busy = cpufreq.busy_cores(c0, c1, t1 - t0, CLK_TCK)
        if busy is not None and p0 and p1 and p0["pid"] == p1["pid"]:
            busy -= cpu_delta(p0, p1)["cpu_s"] / (t1 - t0)
        return busy

    def apply_governor(self, governor: str) -> None:
        cpus = cpufreq.set_all(governor, SYSFS_CPU)
        self.current_governor = governor
        self.state.setdefault("governor_changes", []).append(
            {"governor": governor, "cpus": len(cpus), "t": time.time()})
        print(f"[{time.strftime('%H:%M:%S')}] governor {governor} on {len(cpus)} CPUs", flush=True)

    def probe_perf(self) -> list[str]:
        if shutil.which("perf") is None:
            self.flags["perf_reason"] = "perf not installed"
            return []
        ok = []
        for ev in DEFAULT_EVENTS:
            res = self.host.run(["perf", "stat", "-x,", "-e", ev, "--", "true"], check=False, timeout=30)
            if res.returncode == 0 and "<not supported>" not in res.stderr:
                ok.append(ev)
        if not ok:
            self.flags["perf_reason"] = ("no perf event countable (perf_event_paranoid or PMU "
                                         "held by LIKWID)")
        return ok

    def install(self) -> None:
        self.prior_nvra = None
        res = self.host.run(["rpm", "-q", "hpcperfstatsd"], check=False)
        if res.returncode == 0:
            self.prior_nvra = res.stdout.strip()
        self.was_active = self.systemctl("is-active", "--quiet", UNIT, check=False).returncode == 0
        self.was_enabled = self.systemctl("is-enabled", "--quiet", UNIT, check=False).returncode == 0
        self.saved_conf = None
        if Path(SITE_CONF).is_file():
            self.saved_conf = self.raw / "site_conf.saved"
            _write_private(self.saved_conf, Path(SITE_CONF).read_text())
        restore = self.out / "restore.sh"
        restore.write_text(restore_script_text(
            self.saved_conf, self.was_active, self.was_enabled, self.measure_queue,
            governor_lines=cpufreq.restore_lines(self.saved_governors, SYSFS_CPU)))
        restore.chmod(0o700)
        self.state.update(prior_nvra=self.prior_nvra, was_active=self.was_active,
                          was_enabled=self.was_enabled)
        self.host.run(["rpm", "-Uvh", "--replacepkgs", str(self.rpm)], timeout=900)
        self.systemctl("stop", UNIT, check=False)
        _write_private(Path(SITE_CONF), self.conf_text)
        installed = parse_conf(Path(SITE_CONF).read_text())
        if installed.get("server") != self.conf["server"] or installed.get("queue") != self.conf["queue"]:
            raise PreflightError("site conf server/queue mismatch after rpm -Uvh; aborting")
        if Path(SYSCONFIG).is_file():
            errs = check_sysconfig(Path(SYSCONFIG).read_text())
            if errs:
                raise PreflightError("; ".join(errs))
        self.systemctl("daemon-reload")

    def provenance(self) -> None:
        props = self.systemctl("show", UNIT, "-p", ",".join(UNIT_PROPS), check=False).stdout
        unit = dict(line.split("=", 1) for line in props.splitlines() if "=" in line)
        self.caps = _load_caps(self.args.build_info)
        prov = {"host": provenance.host_info(),
                "build": provenance.build_info(self.rpm, self.args.build_info, self.conf_text,
                                               CHECKOUT, unit, self.prior_nvra),
                "harness": {"version": HARNESS_VERSION, "schema": profile_report.PROFILE_SCHEMA_VERSION,
                            "argv": sys.argv[1:], "harness_cpu": self.harness_cpu,
                            "fwq_cpus": self.fwq_cpus, "perf_events": self.perf_events}}
        (self.raw / "provenance.json").write_text(json.dumps(prov, indent=2, default=str) + "\n")

    # -- legs
    def set_dropin(self, text: str | None, sample_freq: float | None = None) -> None:
        if text is None:
            Path(DROPIN_PATH).unlink(missing_ok=True)
        else:
            _write_private(Path(MEASURE_CONF), measure_conf_text(self.conf_text, sample_freq))
            Path(DROPIN_DIR).mkdir(parents=True, exist_ok=True)
            Path(DROPIN_PATH).write_text(text)
        self.systemctl("daemon-reload")

    def start_sampler(self, path: Path, seconds: int) -> subprocess.Popen:
        cmd = ["taskset", "-c", str(self.harness_cpu), sys.executable,
               str(MEASUREMENT_ROOT / "bin" / "sample_proc.py"), "--out", str(path),
               "--duration", str(seconds), "--comm", "hpcperfstatsd", "--amqp-port",
               str(self.amqp_port), "--cpufreq-root", str(SYSFS_CPU)]
        if shutil.which("taskset") is None:
            cmd = cmd[3:]
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=open(path.with_suffix(".err"), "w"))

    def build_profiler(self) -> Path:
        """Build an --enable-metric-profiler hpcperfstatsd from the RPM's own dist tarball.

        Same sources and embedded static prefix as the release RPM, built under raw/ (never in
        monitor/.build-static), so the only difference from production is the profiler flag.
        """
        if self.args.build_info is None:
            raise PreflightError("--profile-collectors needs --build-info (to find SOURCES/) or "
                                 "--profiler-binary")
        tarballs = sorted((Path(self.args.build_info).parent / "SOURCES").glob("hpcperfstats-*.tar.gz"))
        if len(tarballs) != 1:
            raise PreflightError(f"expected one dist tarball under SOURCES/, found {len(tarballs)}")
        work = self.raw / "profiler-build"
        work.mkdir(parents=True, exist_ok=True)
        self.host.run(["tar", "-xzf", str(tarballs[0]), "-C", str(work)], timeout=600)
        src = work / tarballs[0].name[: -len(".tar.gz")]
        env = {**os.environ, "PREFIX": str(src / "embedded-static-prefix"), "SKIP_DEPS": "1",
               "HPC_BUNDLE_RELEASE_BUILD": "1"}
        with open(self.raw / "profiler-build.log", "w") as log:
            res = subprocess.run(["./scripts/build_static_bundle.sh", "--enable-metric-profiler"],
                                 cwd=src, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
        exe = src / ".build-static" / "src" / "hpcperfstatsd"
        if res.returncode != 0 or not exe.is_file():
            raise PreflightError("profiler build failed; see raw/profiler-build.log")
        return exe

    def precheck_fit(self, seed: int) -> None:
        """Before any change: size the schedule from the installed daemon's last startup line.

        A fresh node has no such line; then the fit is checked after discovery and an
        unfittable schedule aborts there (the restore path still runs).
        """
        res = self.host.run(["journalctl", "-u", UNIT, "-o", "cat", "-n", "2000", "--no-pager"],
                            check=False)
        types = journal_types.last_enabled_types(res.stdout or "")
        if not types:
            return
        try:
            groups = driver_map.groups_for_enabled(types, _load_caps(self.args.build_info))
        except driver_map.UnmappedTypeError:
            return  # the new build may map it; discovery decides
        _schedule(self.args, groups, seed, _load_caps(self.args.build_info), self.governors)

    def discover(self) -> list[str]:
        self.set_dropin(None)
        t0 = time.time()
        self.systemctl("restart", UNIT)
        types, _, text = self.wait_enabled_types(t0)
        (self.raw / "discovery_journal.txt").write_text(text)
        if types is None:
            raise PreflightError("daemon logged no `Enabled types:` line within "
                                 f"{TYPES_WAIT_SECONDS} s of start (see raw/discovery_journal.txt)")
        time.sleep(max(0, DISCOVERY_SECONDS - (time.time() - t0)))
        return types

    def run_leg(self, leg: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        a = self.args
        leg_dir = self.raw / "legs" / leg["leg_id"]
        leg_dir.mkdir(parents=True, exist_ok=True)
        settle, quiet = plan["settle_seconds"], plan["leg_seconds"]
        load = plan["fwq_seconds"] if leg["fwq"] else 0
        meta: dict[str, Any] = {k: leg.get(k) for k in ("leg_id", "kind", "block", "group",
                                                         "brackets", "sample_freq", "base", "env",
                                                         "marker", "governor")}
        meta["status"] = "ok"
        meta["governor_observed"] = sorted({g for g in cpufreq.read_state(SYSFS_CPU)["governors"]
                                            .values() if g})
        cad = provenance.parse_monitor_conf(self.conf_text)
        if leg["kind"] == "CADENCE":
            cad["sample_freq"] = leg["sample_freq"]
        meta["cadence"] = {k: cad.get(k) for k in ("sample_freq", "sample_freq_slow", "send_freq",
                                                    "enable_slow_tier")}
        groups = leg_groups(self.groups, plan)
        disabled = driver_map.disable_csv(leg["kind"], self.active, groups, leg.get("group"))
        off = set((disabled or "").split(",")) - {""}
        meta["intended_types"] = [t for t in self.active if t not in off] if leg["kind"] != "OFF" else []
        t0 = time.time()
        if leg["kind"] == "OFF":
            self.set_dropin(None)
            self.systemctl("stop", UNIT)
        else:
            binary = DAEMON
            if leg["kind"] == "PROFILE":
                Path(MEASURE_DIR).mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.profiler_src, PROFILER_BINARY)
                os.chmod(PROFILER_BINARY, 0o755)
                binary = PROFILER_BINARY
            self.set_dropin(dropin_text(leg["kind"], self.active, groups, leg.get("group"),
                                        binary=binary, env=leg.get("env")), leg.get("sample_freq"))
            t0 = time.time()
            self.systemctl("restart", UNIT)
        meta["t_start"] = t0
        sampler = self.start_sampler(leg_dir / "samples.jsonl", settle + quiet + load + 3)
        if leg["kind"] != "OFF":
            types, startup, _journal = self.wait_enabled_types(t0)
            meta["startup_wall_s"] = startup
            if types is None:
                meta["status"] = "failed"
                meta["verified_types"], meta["missing_types"] = [], meta["intended_types"]
                meta["failure"] = "no Enabled types line (daemon exited or misbehaved)"
            else:
                meta["verified_types"] = types
                meta["missing_types"] = [t for t in meta["intended_types"] if t not in types]
        time.sleep(max(0, t0 + settle - time.time()))
        meta["phases"] = {"settle_end": time.time()}
        perf_proc = None
        pid = self.main_pid() if leg["kind"] != "OFF" else None
        if leg["kind"] != "OFF" and pid is None and meta["status"] == "ok":
            meta["status"] = "failed"
            meta["failure"] = "no daemon MainPID after the settle period"
        if pid and self.perf_events:
            perf_proc = subprocess.Popen(
                ["perf", "stat", "-x,", "-e", ",".join(self.perf_events), "-p", str(pid), "-o",
                 str(leg_dir / "perf.csv"), "--", "sleep", str(quiet)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            meta["perf_seconds"] = quiet
        time.sleep(max(0, meta["phases"]["settle_end"] + quiet - time.time()))
        if perf_proc:
            perf_proc.wait(timeout=60)
        meta["phases"]["quiet_end"] = time.time()
        if leg["kind"] == "PROFILE":
            # SIGINT stop makes the daemon print its cumulative metric-profiler report.
            self.systemctl("stop", UNIT)
            meta["profiler_binary_sha256"] = provenance.sha256_file(Path(PROFILER_BINARY))
        if leg["fwq"]:
            self.host.run([str(self.fwq_exe), "-c", fmt_cpus(self.fwq_cpus), "-s", str(load), "-w",
                           str(self.fwq_work), "-o", str(self._mk(leg_dir / "fwq"))],
                          timeout=load + 120)
        meta["phases"]["fwq_end"] = time.time()
        if leg["fwq"] and a.cyclictest and shutil.which("cyclictest"):
            self.host.run(["cyclictest", "-D", "60", "-m", "-q", "-h", "200", "-p", "80", "-a",
                           fmt_cpus(self.fwq_cpus), "-t", str(len(self.fwq_cpus))], check=False,
                          timeout=180)
        sampler.wait(timeout=120)
        if leg["kind"] in MEASURE_QUEUE_KINDS:
            # After the sampler stopped, so the drain is never inside a measured window.
            meta["schema_capture"] = self.capture_schema(leg_dir)
        if leg["kind"] != "OFF":
            end_pid = self.main_pid()
            meta["pid_start"], meta["pid_end"] = pid, end_pid
            if pid and end_pid != pid and leg["kind"] != "PROFILE":
                meta["daemon_restarted"] = True
            (leg_dir / "journal.txt").write_text(self.journal_since(t0 - 1))
        (leg_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        return meta

    def capture_schema(self, leg_dir: Path) -> str:
        """Drain ``<queue>_measure``; keep the leg's last ``$`` schema message as schema.txt."""
        res = rmq_cleanup.drain_measure_queue(self.conf, mgmt_port=self.args.rmq_mgmt_port)
        if res["status"] != "ok":
            return res["status"]
        text = schema_capture.last_schema_message(res["messages"])
        if text is None:
            return f"no schema message among {len(res['messages'])} drained"
        (leg_dir / "schema.txt").write_text(text)
        return "ok"

    @staticmethod
    def _mk(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -- cleanup
    def cleanup(self) -> None:
        if self.cleanup_done or not getattr(self, "installed", False):
            return
        self.cleanup_done = True
        res: dict[str, Any] = {}
        for step, fn in (
            ("governor", lambda: "; ".join(cpufreq.restore(self.saved_governors, SYSFS_CPU))
             or ("ok" if self.saved_governors else "not changed (--governors none)")),
            ("dropin", lambda: (Path(DROPIN_PATH).unlink(missing_ok=True),
                                _rmdir_if_empty(Path(DROPIN_DIR)),
                                shutil.rmtree(MEASURE_DIR, ignore_errors=True),
                                self.systemctl("daemon-reload", check=False))),
            ("measure_queue", lambda: rmq_cleanup.delete_measure_queue(
                self.conf, mgmt_port=self.args.rmq_mgmt_port)),
            ("site_conf", lambda: _write_private(Path(SITE_CONF), self.saved_conf.read_text())
             if self.saved_conf else "left --conf installed (no prior site conf)"),
            ("service_state", lambda: (
                self.systemctl("enable" if self.was_enabled else "disable", UNIT, check=False),
                self.systemctl("restart" if self.was_active else "stop", UNIT, check=False))),
        ):
            try:
                out = fn()
                res[step] = out if isinstance(out, (dict, str)) else "ok"
            except Exception as err:  # keep restoring the rest
                res[step] = f"FAILED: {err}"
        mq = res.get("measure_queue")
        if isinstance(mq, dict):
            self.flags["measure_queue_left"] = mq.get("measure_queue_left", True)
            if mq.get("admin_command"):
                print(f"Measurement queue left on the broker; ask the admin to run:\n  "
                      f"{mq['admin_command']}", file=sys.stderr)
        self.state["cleanup"] = res
        self.write_run_json()

    def write_run_json(self) -> None:
        doc = {"schema": 1, "harness_version": HARNESS_VERSION, "flags": self.flags,
               "groups": getattr(self, "groups", {}), "active_types": getattr(self, "active", []),
               "schedule": getattr(self, "plan", None),
               "planned_leg_ids": [leg["leg_id"] for leg in (getattr(self, "plan", None) or {}).get("legs", [])],
               "state": self.state, "fwq": {"work": getattr(self, "fwq_work", None),
                                           "quantum_us": self.args.fwq_quantum_us},
               "started": self.started}
        (self.raw / "run.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")

    def summarize(self) -> None:
        try:
            profile_report.write_outputs(self.out)
        except Exception as err:  # a report bug must not stop the measurement
            print(f"WARN: summarize failed: {err}", file=sys.stderr)

    # -- main
    def execute(self) -> int:
        a = self.args
        if not self.rpm.is_file():
            raise PreflightError(f"RPM not found: {self.rpm}")
        info = provenance.host_info()
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        name = f"{info['short']}_{info['arch']}_{provenance.slug(info['cpu_model'])}_{stamp}"
        self.out = (a.out or MEASUREMENT_ROOT / "results" / name).resolve()
        self.raw = self.out / "raw"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.host = Host(self.raw / "commands.log")
        self.flags = {}
        self.preflight()
        seed = a.seed if a.seed is not None else int(self.started) % 1_000_000
        self.precheck_fit(seed)
        if a.profile_collectors:
            self.profiler_src = a.profiler_binary or self.build_profiler()
            if not Path(self.profiler_src).is_file():
                raise PreflightError(f"profiler binary not found: {self.profiler_src}")
        self.installed = True
        try:
            self.install()
            self.provenance()
            self.fwq_exe = fwq.build(self.raw / "bin")
            cal = self.host.run([str(self.fwq_exe), "--calibrate", str(a.fwq_quantum_us), "-c",
                                 str(self.fwq_cpus[0])])
            self.fwq_work = int(cal.stdout.strip())
            self.active = self.discover()
            self.groups = driver_map.groups_for_enabled(self.active, self.caps)
            self.plan = _schedule(a, self.groups, seed, self.caps, self.governors)
            self.write_run_json()
            print_plan(a, self.active, self.groups, self.plan, out=lambda s: print(s, flush=True))
            self.current_governor = None
            for leg in self.plan["legs"]:
                if leg.get("governor") and leg["governor"] != self.current_governor:
                    self.apply_governor(leg["governor"])
                print(f"[{time.strftime('%H:%M:%S')}] leg {leg['leg_id']}", flush=True)
                self.run_leg(leg, self.plan)
                self.summarize()
                summary = json.loads((self.raw / "legs" / leg["leg_id"] / "summary.json").read_text())
                if summary.get("extra_types"):
                    raise PreflightError(f"leg {leg['leg_id']}: the daemon kept types it was told to "
                                         f"disable: {','.join(summary['extra_types'])}; aborting")
                if summary.get("contaminated"):
                    self.flags.setdefault("contaminated_legs", []).append(leg["leg_id"])
                    if not a.force:
                        raise PreflightError(f"leg {leg['leg_id']} contaminated by outside load "
                                             "(non-daemon CPU above threshold); aborting")
                    # --force: the whole run becomes a smoke run that is never cited.
                    self.flags["not_idle"] = "smoke only"
                    self.flags.setdefault("not_idle_reasons", []).append(
                        f"leg {leg['leg_id']} contaminated by outside load")
                    self.write_run_json()
                    self.summarize()
            self.state["completed"] = True
        finally:
            self.cleanup()
            self.summarize()
            print(f"Result directory: {self.out}")
        return 0


def dry_run_governors(spec: str, out: Callable[[str], None] = print) -> list[str]:
    """Governors as this host would resolve them; on a host that cannot, the names as given."""
    try:
        return cpufreq.plan_governors(spec, SYSFS_CPU)["governors"]
    except cpufreq.GovernorError as err:
        out(f"WARNING: {err} (the real run would stop at preflight on this host)")
        return cpufreq.parse_spec(spec)


def _install_signal_handlers() -> None:
    def handler(signum, _frame):
        raise SystemExit(128 + signum)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, handler)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.dry_run:
            types = [t for t in (args.dry_run_types or "").split(",") if t] or [
                "host_cpu", "host_mem", "host_block", "host_net", "host_cpu_hw"]
            caps = _load_caps(args.build_info)
            groups = driver_map.groups_for_enabled(types, caps)
            seed = args.seed if args.seed is not None else int(time.time()) % 1_000_000
            governors = dry_run_governors(args.governors)
            print_plan(args, sorted(types), groups, _schedule(args, groups, seed, caps, governors))
            return 0
        if os.geteuid() != 0:
            print("ERROR: the measurement must run as root (sudo); use --dry-run to preview",
                  file=sys.stderr)
            return 1
        _install_signal_handlers()
        return Run(args).execute()
    except schedule.ScheduleDoesNotFit as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 3
    except (PreflightError, driver_map.UnmappedTypeError, cpufreq.GovernorError) as err:
        print(f"ERROR: preflight: {err}", file=sys.stderr)
        return 4
