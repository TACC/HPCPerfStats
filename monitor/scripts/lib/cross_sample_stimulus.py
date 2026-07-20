"""Short-lived host load during live cross-sample capture so must-move canaries can advance."""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StimulusResult:
    touched_lustre: bool
    lustre_mount: str | None


def _probe_lustre_mount() -> str | None:
    """Return a writable Lustre mount path if one is visible, else None."""
    try:
        from .host_live_probes import probe_lustre_obd_devices
    except ImportError:
        return None
    mounts = probe_lustre_obd_devices("llite")
    for mnt in mounts:
        p = Path(mnt)
        if p.is_dir() and os.access(p, os.W_OK | os.R_OK | os.X_OK):
            return str(p)
    return None


def _cpu_burn(stop: threading.Event) -> None:
    x = 0
    while not stop.is_set():
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        if x == 0:
            x = 1


def _io_once(path: Path, nbytes: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = b"\x5a" * min(nbytes, 1024 * 1024)
    remaining = nbytes
    with path.open("wb") as fh:
        while remaining > 0:
            chunk = blob if remaining >= len(blob) else blob[:remaining]
            fh.write(chunk)
            remaining -= len(chunk)
        fh.flush()
        os.fsync(fh.fileno())
    with path.open("rb") as fh:
        while fh.read(1024 * 1024):
            pass
    try:
        path.unlink()
    except OSError:
        pass


def _io_loop(stop: threading.Event, path: Path, nbytes: int, interval_sec: float) -> None:
    while not stop.wait(interval_sec):
        try:
            _io_once(path, nbytes)
        except OSError:
            break


def _touch_lustre(mount: str, stop: threading.Event) -> bool:
    """Write/read/unlink a tiny file under a Lustre mount. Returns True on success."""
    target = Path(mount) / f".hpcperfstats_cross_sample_stim_{os.getpid()}"
    try:
        target.write_bytes(b"stim\n")
        _ = target.read_bytes()
        # Keep briefly so collectors mid-interval can observe ops; remove on stop.
        stop.wait(0.05)
        return True
    except OSError:
        return False
    finally:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def cross_sample_stimulus(
    *,
    n_cpu_workers: int | None = None,
    tmp_dir: Path | None = None,
    io_bytes: int = 4 * 1024 * 1024,
    io_interval_sec: float = 2.0,
    enable_lustre: bool = True,
):
    """Run CPU + /tmp IO (+ optional Lustre) load until the context exits.

    Always stops background threads on exit so validate never leaves load running.
    """
    stop = threading.Event()
    threads: list[threading.Thread] = []
    tmp_root = Path(tmp_dir) if tmp_dir is not None else Path("/tmp")
    stim_path = tmp_root / f"hpcperfstats_cross_sample_stim_{os.getpid()}.bin"
    lustre_mount = _probe_lustre_mount() if enable_lustre else None
    touched_lustre = False

    n = n_cpu_workers if n_cpu_workers is not None else min(4, max(1, os.cpu_count() or 1))
    for i in range(n):
        t = threading.Thread(target=_cpu_burn, args=(stop,), name=f"cs-stim-cpu-{i}", daemon=True)
        t.start()
        threads.append(t)

    io_thread = threading.Thread(
        target=_io_loop,
        args=(stop, stim_path, io_bytes, io_interval_sec),
        name="cs-stim-io",
        daemon=True,
    )
    io_thread.start()
    threads.append(io_thread)

    # Kick one IO immediately so short waits still see disk activity.
    try:
        _io_once(stim_path, io_bytes)
    except OSError:
        pass

    if lustre_mount is not None:
        touched_lustre = _touch_lustre(lustre_mount, stop)

    try:
        yield StimulusResult(touched_lustre=touched_lustre, lustre_mount=lustre_mount)
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=2.0)
        try:
            stim_path.unlink(missing_ok=True)
        except OSError:
            pass
        if lustre_mount is not None:
            leftover = Path(lustre_mount) / f".hpcperfstats_cross_sample_stim_{os.getpid()}"
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                pass
