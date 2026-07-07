"""Discover and parse hpcperfstatsd active configuration for cross-sample timing."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Contract: hpcperfstats.spec hpc_debug_build + rpm_debug_shm_verify.sh FAST/FULL
DEBUG_RPM_SAMPLE_FREQ = 30.0
DEBUG_RPM_SAMPLE_FREQ_SLOW = 60.0

# C globals when no conf file (monitor_daemon.c)
C_DEFAULT_SAMPLE_FREQ = 30.0
C_DEFAULT_SAMPLE_FREQ_SLOW = 600.0
C_DEFAULT_SEND_FREQ = 300.0
C_DEFAULT_ENABLE_SLOW_TIER = 1

INSTALLED_CONF = Path("/etc/hpcperfstats/hpcperfstats.conf")

CONF_ARG_FLAGS = frozenset(
    {"-c", "--configfile", "--config-file", "--conf_file"}
)


@dataclass(frozen=True)
class DaemonTiming:
    sample_freq: float
    sample_freq_slow: float
    send_freq: float
    enable_slow_tier: bool


@dataclass(frozen=True)
class ActiveConf:
    timing: DaemonTiming
    conf_path: Path | None
    source: str  # report label: explicit, cmdline, systemd, installed, c_defaults


@dataclass(frozen=True)
class WaitBounds:
    fast_timeout_sec: float
    full_timeout_sec: float
    fast_cadence_min: float
    fast_cadence_max: float
    full_cadence_min: float
    full_cadence_max: float


def wait_bounds_from_timing(t: DaemonTiming) -> WaitBounds:
    sf = t.sample_freq
    ss = t.sample_freq_slow if t.enable_slow_tier else t.sample_freq
    return WaitBounds(
        fast_timeout_sec=sf * 2.0 + 15.0,
        full_timeout_sec=ss * 1.2 + 30.0,
        fast_cadence_min=0.5 * sf,
        fast_cadence_max=2.5 * sf,
        full_cadence_min=0.5 * ss,
        full_cadence_max=1.5 * ss,
    )


def is_debug_rpm_timing(t: DaemonTiming) -> bool:
    return (
        abs(t.sample_freq - DEBUG_RPM_SAMPLE_FREQ) < 0.01
        and abs(t.sample_freq_slow - DEBUG_RPM_SAMPLE_FREQ_SLOW) < 0.01
        and t.enable_slow_tier
    )


def _parse_double(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        v = float(value)
    except ValueError:
        return None
    if v != v:  # NaN
        return None
    return v


def parse_daemon_conf(path: Path) -> DaemonTiming:
    """Parse timing keys from an hpcperfstats.conf file (C-aligned defaults per key)."""
    timing = DaemonTiming(
        sample_freq=C_DEFAULT_SAMPLE_FREQ,
        sample_freq_slow=C_DEFAULT_SAMPLE_FREQ_SLOW,
        send_freq=C_DEFAULT_SEND_FREQ,
        enable_slow_tier=bool(C_DEFAULT_ENABLE_SLOW_TIER),
    )
    if not path.is_file():
        return timing

    sf = timing.sample_freq
    ss = timing.sample_freq_slow
    send = timing.send_freq
    slow_tier = timing.enable_slow_tier

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key = None
        val = None
        for sep in ("=", ":", None):
            if sep is None:
                parts = line.split(None, 1)
                if len(parts) == 2:
                    key, val = parts[0], parts[1]
                break
            if sep in line:
                key, val = line.split(sep, 1)
                key = key.strip()
                val = val.strip()
                break
        if key is None or val is None:
            continue
        if key == "sample_freq":
            v = _parse_double(val)
            if v is not None:
                sf = v
        elif key == "sample_freq_slow":
            v = _parse_double(val)
            if v is not None:
                ss = v
        elif key == "send_freq":
            v = _parse_double(val)
            if v is not None:
                send = v
        elif key == "enable_slow_tier":
            slow_tier = val.strip() not in ("0", "false", "False", "no", "off")
        elif key == "freq":
            v = _parse_double(val)
            if v is not None:
                sf = v

    if sf <= 0:
        sf = 1.0
    if sf < 0.1:
        sf = 0.1
    if ss <= 0:
        ss = sf
    if ss < sf:
        ss = sf
    if send <= 0:
        send = 1.0

    return DaemonTiming(
        sample_freq=sf,
        sample_freq_slow=ss,
        send_freq=send,
        enable_slow_tier=slow_tier,
    )


def load_fixture_timing(path: Path) -> DaemonTiming:
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    return DaemonTiming(
        sample_freq=float(data.get("sample_freq", C_DEFAULT_SAMPLE_FREQ)),
        sample_freq_slow=float(data.get("sample_freq_slow", C_DEFAULT_SAMPLE_FREQ_SLOW)),
        send_freq=float(data.get("send_freq", C_DEFAULT_SEND_FREQ)),
        enable_slow_tier=bool(data.get("enable_slow_tier", 1)),
    )


def _find_daemon_pids() -> list[int]:
    pids: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return pids
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        cmdline_path = entry / "cmdline"
        try:
            raw = cmdline_path.read_bytes()
        except OSError:
            continue
        if b"hpcperfstatsd" in raw:
            pids.append(int(entry.name))
    return sorted(pids)


def _conf_from_cmdline(pid: int) -> Path | None:
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    args = [a.decode("utf-8", errors="replace") for a in raw.split(b"\0") if a]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in CONF_ARG_FLAGS and i + 1 < len(args):
            return Path(args[i + 1])
        if arg.startswith("-c") and len(arg) > 2:
            return Path(arg[2:])
        i += 1
    return None


def _conf_from_systemd(unit: str) -> Path | None:
    try:
        out = subprocess.run(
            ["systemctl", "show", unit, "-p", "ExecStart", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    text = out.stdout.strip()
    m = re.search(r"(?:^|\s)-c\s+(\S+)", text)
    if m:
        return Path(m.group(1))
    m = re.search(r"(?:^|\s)--configfile(?:=|\s+)(\S+)", text)
    if m:
        return Path(m.group(1))
    return None


def discover_active_conf(
    *,
    explicit_conf: Path | None = None,
    systemd_unit: str = "hpcperfstats",
    require_daemon: bool = False,
) -> ActiveConf:
    """Resolve active daemon conf for cross-sample timing (never repo src/hpcperfstats.conf)."""
    if explicit_conf is not None:
        path = explicit_conf.expanduser()
        timing = parse_daemon_conf(path)
        return ActiveConf(timing=timing, conf_path=path, source=f"explicit {path}")

    pids = _find_daemon_pids()
    if pids:
        cmdline_conf = _conf_from_cmdline(pids[0])
        if cmdline_conf is not None:
            path = cmdline_conf
            timing = parse_daemon_conf(path)
            return ActiveConf(
                timing=timing,
                conf_path=path if path.is_file() else None,
                source=f"pid {pids[0]} cmdline {path}",
            )

    systemd_conf = _conf_from_systemd(systemd_unit)
    if systemd_conf is not None and systemd_conf.is_file():
        timing = parse_daemon_conf(systemd_conf)
        return ActiveConf(
            timing=timing,
            conf_path=systemd_conf,
            source=f"systemd {systemd_conf}",
        )

    if INSTALLED_CONF.is_file():
        timing = parse_daemon_conf(INSTALLED_CONF)
        return ActiveConf(
            timing=timing,
            conf_path=INSTALLED_CONF,
            source=f"installed default {INSTALLED_CONF}",
        )

    if pids:
        timing = DaemonTiming(
            sample_freq=C_DEFAULT_SAMPLE_FREQ,
            sample_freq_slow=C_DEFAULT_SAMPLE_FREQ_SLOW,
            send_freq=C_DEFAULT_SEND_FREQ,
            enable_slow_tier=bool(C_DEFAULT_ENABLE_SLOW_TIER),
        )
        return ActiveConf(
            timing=timing,
            conf_path=None,
            source=f"pid {pids[0]} no -c; C defaults",
        )

    if require_daemon:
        raise RuntimeError("no running hpcperfstatsd and no --conf")

    timing = DaemonTiming(
        sample_freq=C_DEFAULT_SAMPLE_FREQ,
        sample_freq_slow=C_DEFAULT_SAMPLE_FREQ_SLOW,
        send_freq=C_DEFAULT_SEND_FREQ,
        enable_slow_tier=bool(C_DEFAULT_ENABLE_SLOW_TIER),
    )
    return ActiveConf(timing=timing, conf_path=None, source="no daemon; C defaults")
