"""Live host probes shared by build_message_expectations and live spot-check."""
from __future__ import annotations

import os
import re
import socket
from pathlib import Path

PROC_STAT_CPU_RE = re.compile(r"^cpu(\d+)\s")
_NODE_MEMINFO_RE = re.compile(r"^Node \d+ (\w+):\s+(\d+)")
_IFF_UP = 0x1


def _read_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fd:
            return fd.readlines()
    except OSError:
        return []


def probe_host_fqdn() -> str:
    try:
        return socket.getfqdn()
    except OSError:
        return socket.gethostname()


def probe_cpu_devices() -> list[str]:
    devs: list[str] = []
    for line in _read_lines("/proc/stat"):
        m = PROC_STAT_CPU_RE.match(line)
        if m:
            devs.append(m.group(1))
    return devs


def probe_net_devices() -> list[str]:
    """Match host_net collector: IFF_UP interfaces from /sys/class/net (net.c)."""
    net_base = Path("/sys/class/net")
    if not net_base.is_dir():
        return []
    devs: list[str] = []
    for p in sorted(net_base.iterdir()):
        if not p.is_dir():
            continue
        flags_path = p / "flags"
        try:
            flags = int(flags_path.read_text(encoding="utf-8").strip(), 0)
        except (OSError, ValueError):
            continue
        if flags & _IFF_UP:
            devs.append(p.name)
    return devs


def probe_ib_devices() -> list[str]:
    """host_ib devices: non-OPA HCAs only (hfi1_* is host_opa)."""
    ib_base = Path("/sys/class/infiniband")
    if not ib_base.is_dir():
        return []
    devs: list[str] = []
    for hca in sorted(ib_base.iterdir()):
        if not hca.is_dir():
            continue
        if _hca_is_opa_hfi(hca.name):
            continue
        ports = hca / "ports"
        if not ports.is_dir():
            continue
        for port in sorted(ports.iterdir()):
            if port.is_dir() and port.name.isdigit():
                devs.append(f"{hca.name}.{port.name}")
    return devs


def _hca_is_opa_hfi(name: str) -> bool:
    """Match ib_hca_is_opa_hfi() in ib_common.c."""
    if not name.startswith("hfi1"):
        return False
    return len(name) == 4 or name[4] == "_"


def _ib_port_logic_active(state_line: str | None) -> bool:
    """Match ib_port_logic_active() in ib_port_state.c (case-sensitive text)."""
    if state_line is None:
        return False
    s = state_line.lstrip()
    if not s:
        return False
    # Numeric prefix 4 == ACTIVE wins (even before inactive/active text).
    num = ""
    for ch in s:
        if ch.isdigit():
            num += ch
        else:
            break
    if num != "" and int(num) == 4:
        return True
    # C uses strstr on the original line (case-sensitive).
    if "inactive" in state_line:
        return False
    return "active" in state_line


def _ib_port_phys_link_up(phys_line: str | None) -> bool:
    """Match ib_port_phys_link_up() in ib_port_state.c (case-sensitive text)."""
    if phys_line is None:
        return False
    s = phys_line.lstrip()
    if not s:
        return False
    num = ""
    for ch in s:
        if ch.isdigit():
            num += ch
        else:
            break
    if num != "" and int(num) == 5:
        return True
    return "link_up" in phys_line or "linkup" in phys_line


def _ib_port_collectible(hca_ports: Path, port_name: str) -> bool:
    """Match ib_port_collectible() — ACTIVE state or phys LinkUp."""
    port_dir = hca_ports / port_name
    try:
        state = (port_dir / "state").read_text(encoding="utf-8", errors="replace")
    except OSError:
        state = None
    if _ib_port_logic_active(state):
        return True
    try:
        phys = (port_dir / "phys_state").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return _ib_port_phys_link_up(phys)


def probe_opa_devices() -> list[str]:
    """host_opa devices: collectible hfi1_N/P (slash); mirrors opa.c + ib_port_collectible."""
    ib_base = Path("/sys/class/infiniband")
    if not ib_base.is_dir():
        return []
    devs: list[str] = []
    for hca in sorted(ib_base.iterdir()):
        if not hca.is_dir() or not _hca_is_opa_hfi(hca.name):
            continue
        ports = hca / "ports"
        if not ports.is_dir():
            continue
        for port in sorted(ports.iterdir()):
            if not port.is_dir() or not port.name.isdigit():
                continue
            if not _ib_port_collectible(ports, port.name):
                continue
            devs.append(f"{hca.name}/{port.name}")
    return devs


def probe_block_devices() -> list[str]:
    devs: list[str] = []
    for line in _read_lines("/proc/diskstats"):
        parts = line.split()
        if len(parts) < 3:
            continue
        major = int(parts[0])
        if major < 259 and major != 8 and major != 252:
            continue
        name = parts[2]
        if name and not name[-1].isdigit():
            devs.append(name)
    return sorted(set(devs))


def probe_numa_node_devices() -> list[str]:
    """NUMA node ids used as host_mem/host_numa dev (see mem.c, numa.c)."""
    base = Path("/sys/devices/system/node")
    if not base.is_dir():
        return []
    devs: list[str] = []
    for p in sorted(base.iterdir()):
        if not p.is_dir() or not p.name.startswith("node"):
            continue
        node_id = p.name[4:]
        if node_id.isdigit():
            devs.append(node_id)
    return devs


def probe_nfs_mount_devices() -> list[str]:
    """NFS client mount points from /proc/self/mountstats (nfs.c uses mnt as dev)."""
    prefix = "device "
    mounted_on = " mounted on "
    fstype = " with fstype nfs statvers="
    devs: list[str] = []
    for line in _read_lines("/proc/self/mountstats"):
        s = line.strip()
        if not s.startswith(prefix) or mounted_on not in s or fstype not in s:
            continue
        after_mounted = s.split(mounted_on, 1)[1]
        mnt = after_mounted.split()[0] if after_mounted else ""
        if not mnt:
            continue
        ver = s.split(fstype, 1)[1].split()[0] if fstype in s else ""
        if ver not in ("1.0", "1.1"):
            continue
        devs.append(mnt)
    return sorted(set(devs))


def _lustre_sb_mount_map() -> dict[str, str]:
    """Map 16-char Lustre superblock suffix to mount prefix (lustre_obd_to_mnt.c)."""
    lov = Path("/proc/fs/lustre/lov")
    out: dict[str, str] = {}
    if not lov.is_dir():
        return out
    for p in lov.iterdir():
        if not p.is_dir() or p.name.startswith("."):
            continue
        name = p.name
        if len(name) < 17:
            continue
        sb = name[-16:]
        prefix = name[:-17]
        if prefix and all(c in "0123456789abcdef" for c in sb.lower()):
            out[sb] = prefix
    return out


def probe_lustre_obd_devices(obd_subdir: str) -> list[str]:
    """Mount paths for lustre_osc/mdc/llite rows (osc.c, mdc.c, llite.c)."""
    sb_map = _lustre_sb_mount_map()
    base = Path(f"/proc/fs/lustre/{obd_subdir}")
    mounts: list[str] = []
    if not base.is_dir():
        return []
    for p in sorted(base.iterdir()):
        if not p.is_dir() or p.name.startswith("."):
            continue
        name = p.name
        if len(name) < 16:
            continue
        mnt = sb_map.get(name[-16:])
        if mnt:
            mounts.append(mnt)
    return sorted(set(mounts))


def probe_tmpfs_devices() -> list[str]:
    """host_tmpfs only tracks /tmp tmpfs (tmpfs.c)."""
    for line in _read_lines("/proc/mounts"):
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "tmpfs" and parts[1] == "/tmp":
            return ["/tmp"]
    return []


def probe_nvidia_gpu_devices() -> list[str]:
    """GPU row indices 0..N-1 when /dev/nvidiaN exists (nvidia_gpu.c)."""
    devs: list[str] = []
    i = 0
    while Path(f"/dev/nvidia{i}").exists():
        devs.append(str(i))
        i += 1
    return devs


def probe_intel_gpu_devices() -> list[str]:
    """intel_gpu device ids as decimal strings; count Stampede3 PVC lspci lines.

    Mirrors intel_gpu.c XPUM device rows (\"0\"..\"N-1\") when XPUM enumerates
    one id per Data Center GPU Max / Ponte Vecchio PCI function.
    """
    import subprocess

    try:
        out = subprocess.check_output(["lspci", "-nn"], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return []
    n = 0
    for line in out.splitlines():
        low = line.lower()
        if "matrox" in low:
            continue
        class_ok = (
            "display controller" in low
            or "3d controller" in low
            or "vga compatible controller" in low
            or "[0380]" in low
            or "accelerator" in low
        )
        if not class_ok:
            continue
        if (
            "ponte vecchio" in low
            or "data center gpu max" in low
            or "[8086:0bd5]" in low
        ):
            n += 1
    return [str(i) for i in range(n)]


def _edac_mc_root() -> Path:
    env = os.environ.get("HPCPERFSTATS_EDAC_MC_ROOT", "")
    if env:
        return Path(env)
    return Path("/sys/devices/system/edac/mc")


def _dimm_mem_type_is_hbm(mem_type: str) -> bool:
    if not mem_type:
        return False
    upper = mem_type.upper()
    return "HBM" in upper


def probe_edac_mem_classes() -> tuple[bool, bool]:
    """Mirror host_edac_scan_mem_classes() from host_edac_mem_topology.c."""
    root = _edac_mc_root()
    has_ddr = False
    has_hbm = False
    if not root.is_dir():
        return has_ddr, has_hbm
    for mc in sorted(root.iterdir()):
        if not mc.is_dir() or not mc.name.startswith("mc"):
            continue
        for dimm in sorted(mc.iterdir()):
            if not dimm.is_dir() or not dimm.name.startswith("dimm"):
                continue
            speed_path = dimm / "dimm_mem_speed"
            try:
                mtps = int(speed_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                continue
            if mtps <= 0:
                continue
            type_path = dimm / "dimm_mem_type"
            mem_type = ""
            try:
                mem_type = type_path.read_text(encoding="utf-8").strip()
            except OSError:
                mem_type = ""
            if _dimm_mem_type_is_hbm(mem_type):
                has_hbm = True
            else:
                has_ddr = True
    return has_ddr, has_hbm


def _spr_imc_mbox_devices() -> list[str]:
    return [f"mbox{i}" for i in range(16)]


def _spr_imc_hbm_devices() -> list[str]:
    return [f"hbm{i}" for i in range(16)]


def probe_spr_imc_devices(
    has_ddr: bool | None = None,
    has_hbm: bool | None = None,
) -> list[str]:
    """SPR IMC dev ids matching runtime LIKWID eventset selection."""
    if has_ddr is None or has_hbm is None:
        has_ddr, has_hbm = probe_edac_mem_classes()
    if has_ddr and has_hbm:
        return sorted(_spr_imc_mbox_devices() + _spr_imc_hbm_devices())
    if has_ddr:
        return _spr_imc_mbox_devices()
    if has_hbm:
        return _spr_imc_hbm_devices()
    return sorted(_spr_imc_mbox_devices() + _spr_imc_hbm_devices())


def merge_device_lists(
    probed: list[str] | None,
    observed: list[str] | None,
) -> list[str] | None:
    """Union probe + shm-observed devices; observed wins over stale singleton ['-']."""
    if observed:
        obs = sorted(set(observed))
        if probed is None:
            return obs
        if probed == ["-"] and obs != ["-"]:
            return obs
        return sorted(set(probed) | set(obs))
    return probed


def default_devices_for_type(type_name: str) -> list[str] | None:
    if type_name == "host_cpu":
        cpus = probe_cpu_devices()
        return cpus if cpus else None
    if type_name in ("host_cpu_hw", "cpu_counter_metrics"):
        cpus = probe_cpu_devices()
        return cpus if cpus else None
    if type_name == "host_net":
        return probe_net_devices()
    if type_name == "host_ib":
        return probe_ib_devices()
    if type_name == "host_opa":
        return probe_opa_devices()
    if type_name == "host_block":
        return probe_block_devices()
    if type_name in ("host_mem", "host_numa"):
        nodes = probe_numa_node_devices()
        return nodes if nodes else None
    if type_name == "host_nfs":
        devs = probe_nfs_mount_devices()
        return devs if devs else None
    if type_name in ("lustre_osc", "lustre_mdc", "lustre_llite"):
        sub = type_name.replace("lustre_", "")
        devs = probe_lustre_obd_devices(sub)
        return devs if devs else None
    if type_name == "host_tmpfs":
        devs = probe_tmpfs_devices()
        return devs if devs else None
    if type_name == "nvidia_gpu":
        devs = probe_nvidia_gpu_devices()
        return devs if devs else None
    if type_name == "intel_gpu":
        devs = probe_intel_gpu_devices()
        return devs if devs else None
    if type_name == "intel_x86_uncore_imc_spr":
        return probe_spr_imc_devices()
    if type_name == "amd_gpu":
        return ["0"]
    if type_name in (
        "host_vm",
        "host_ps",
        "host_vfs",
        "host_sysv_shm",
        "host_lnet",
        "host_roofline_peak",
    ):
        return ["-"]
    if type_name == "host_proc":
        return None
    return None


def probe_loadavg_scaled() -> dict[str, int]:
    """Monitor host_ps stores load ×100 (see ps.c)."""
    for line in _read_lines("/proc/loadavg"):
        parts = line.split()
        if len(parts) < 3:
            continue
        out: dict[str, int] = {}
        for idx, key in enumerate(("load_1", "load_5", "load_15")):
            whole, _, frac = parts[idx].partition(".")
            try:
                out[key] = int(whole) * 100 + int((frac + "00")[:2])
            except ValueError:
                continue
        return out
    return {}


def probe_numa_mem_kb(node: str) -> dict[str, int]:
    path = f"/sys/devices/system/node/node{node}/meminfo"
    vals: dict[str, int] = {}
    for line in _read_lines(path):
        m = _NODE_MEMINFO_RE.match(line.strip())
        if not m:
            continue
        key = m.group(1)
        if key in ("MemTotal", "MemFree"):
            vals[key] = int(m.group(2))
    return vals


def probe_net_stat(iface: str, stat: str) -> int | None:
    p = Path(f"/sys/class/net/{iface}/statistics/{stat}")
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def probe_uptime_seconds() -> float | None:
    for line in _read_lines("/proc/uptime"):
        parts = line.split()
        if parts:
            try:
                return float(parts[0])
            except ValueError:
                return None
    return None


def probe_machine_arch() -> str | None:
    try:
        return Path("/proc/sys/kernel/arch").read_text(encoding="utf-8").strip()
    except OSError:
        import platform

        return platform.machine() or None
