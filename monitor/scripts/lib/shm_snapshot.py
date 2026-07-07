"""Capture paired shm snapshots for cross-sample validation."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .daemon_conf import DaemonTiming, WaitBounds, wait_bounds_from_timing
from .payload_parse import sample_header_timestamp


@dataclass(frozen=True)
class SnapshotPair:
    kind: str
    ts_a: float
    ts_b: float
    body_a: str
    body_b: str


def read_shm_sample(path: Path) -> tuple[float | None, str, float]:
    body = path.read_text(encoding="utf-8")
    ts = sample_header_timestamp(body)
    mtime = path.stat().st_mtime
    return ts, body, mtime


def wait_for_timestamp_advance(
    path: Path,
    prev_ts: float,
    *,
    timeout_sec: float,
    poll_interval_sec: float = 0.5,
) -> tuple[float, str]:
    deadline = time.time() + timeout_sec
    last_body = ""
    while time.time() < deadline:
        if not path.is_file():
            time.sleep(poll_interval_sec)
            continue
        ts, body, _ = read_shm_sample(path)
        last_body = body
        if ts is not None and ts > prev_ts:
            return ts, body
        time.sleep(poll_interval_sec)
    raise TimeoutError(
        f"{path.name}: timestamp did not advance past {prev_ts} within {timeout_sec}s"
    )


def capture_pair(
    shm_dir: Path,
    kind: str,
    *,
    timing: DaemonTiming,
    bounds: WaitBounds | None = None,
) -> SnapshotPair:
    if bounds is None:
        bounds = wait_bounds_from_timing(timing)
    path = shm_dir / kind
    if not path.is_file():
        raise FileNotFoundError(f"missing shm file: {path}")

    ts_a, body_a, _ = read_shm_sample(path)
    if ts_a is None:
        raise ValueError(f"{path}: no sample header timestamp")

    timeout = bounds.fast_timeout_sec if kind == "fast" else bounds.full_timeout_sec
    ts_b, body_b = wait_for_timestamp_advance(
        path,
        ts_a,
        timeout_sec=timeout,
    )
    return SnapshotPair(kind=kind, ts_a=ts_a, ts_b=ts_b, body_a=body_a, body_b=body_b)


def save_snapshot_pair(
    pair: SnapshotPair,
    save_dir: Path,
    *,
    prefix: str = "",
) -> None:
    save_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{prefix}{pair.kind}" if prefix else pair.kind
    (save_dir / f"t0_{tag}").write_text(pair.body_a, encoding="utf-8")
    (save_dir / f"t1_{tag}").write_text(pair.body_b, encoding="utf-8")


def load_fixture_pair(fixture_dir: Path, kind: str) -> SnapshotPair:
    t0 = fixture_dir / "t0" / kind
    t1 = fixture_dir / "t1" / kind
    if not t0.is_file() or not t1.is_file():
        raise FileNotFoundError(f"fixture missing t0/t1 {kind} under {fixture_dir}")
    ts_a, body_a, _ = read_shm_sample(t0)
    ts_b, body_b, _ = read_shm_sample(t1)
    if ts_a is None or ts_b is None:
        raise ValueError(f"fixture {kind}: missing header timestamp")
    return SnapshotPair(kind=kind, ts_a=ts_a, ts_b=ts_b, body_a=body_a, body_b=body_b)
