"""Per-SM-per-clock tensor FLOP rates from a GPU calibration run (stdlib only; node python3).

Used by ``bin/gpu_tensor_calibrate.sh run``. Inputs in the run directory:

* ``load_*.txt``: output of ``gpu_tensor_calibrate`` (one cuBLAS GEMM kind and size per file);
  its result line carries the exact FLOP total (``calls x 2*n^3``), the cudaEvent-timed seconds,
  the device CC and SM count, and the wall-clock edges of the timed window;
* ``schema.txt`` + ``samples/*.txt``: every DEBUG ``/dev/shm`` ``fast`` payload the daemon wrote
  during the run, parsed with :mod:`lib.grace_scf_verify`.

For each load, the daemon's own ``nvidia_gpu`` row of the device gives the pipe duty cycle
(``tensor_hmma_active`` / ``tensor_dmma_active``, in %) and ``gpu_sm_clock`` (MHz), averaged over
the samples whose whole interval lies inside the timed window; then
``per_sm_clock = FLOP/s / (activity x SMs x clock)``, the quantity ``g_peaks`` in
``src/nvidia_gpu_estimate.c`` holds per pipe. The two sizes of a kind must agree within
``AGREE``; the result is printed as that table's row for the device CC.
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import re
import sys
from typing import Any

from lib.grace_scf_verify import parse_payload, parse_schema

ACTIVITY_KEY = {"hmma": "tensor_hmma_active", "dmma": "tensor_dmma_active"}
PIPE_ENUM = {"hmma": "NVIDIA_GPU_PIPE_HMMA", "dmma": "NVIDIA_GPU_PIPE_DMMA"}
CLOCK_KEY = "gpu_sm_clock"
IDLE_PCT = 5.0  # pipe activity (%) at or below which the pipe counts as idle
AGREE = 0.05  # largest relative spread between the two sizes of a kind
MIN_INSIDE = 2  # sample intervals wholly inside a load window needed for an average
STEADY_PCT = 5.0  # largest activity spread (percentage points) across the averaged samples


def parse_load(text: str) -> dict[str, str] | None:
    """``key=value`` fields of the calibrator's result line.

    Args:
        text: Full output of one ``gpu_tensor_calibrate --kind K --n N`` run.

    Returns:
        The fields of the last ``gpu_tensor_calibrate kind=...`` line carrying ``flops=``, or
        None when the run printed no result (an ``error=`` line or a crash).
    """
    found = None
    for line in text.splitlines():
        if line.startswith("gpu_tensor_calibrate kind=") and " flops=" in line:
            found = dict(tok.split("=", 1) for tok in line.split()[1:] if "=" in tok)
    return found


def device_samples(texts: list[str], schema: dict[str, list[str]], dev: str
                   ) -> list[tuple[float, dict[str, int] | None]]:
    """``(header ts, nvidia_gpu row of dev or None)`` per distinct payload, in time order."""
    by_ts: dict[float, dict[str, int] | None] = {}
    for text in texts:
        head = text.split(None, 1)
        try:
            ts = float(head[0])
        except (IndexError, ValueError):
            continue
        by_ts[ts] = parse_payload(text, schema).get(("nvidia_gpu", dev))
    return sorted(by_ts.items())


def _value(row: dict[str, int] | None, key: str, dev: str) -> tuple[int | None, str]:
    if row is None:
        return None, f"no nvidia_gpu {dev} row"
    if key not in row:
        return None, f"nvidia_gpu {dev} has no {key}"
    if row[key] < 0:
        return None, f"nvidia_gpu {dev} {key} emitted sentinel {row[key]}"
    return row[key], ""


def evaluate_load(load: dict[str, str], samples: list[tuple[float, dict[str, int] | None]],
                  kind: str, dev: str = "0") -> dict[str, Any]:
    """Verdict and per-SM-per-clock rate of one load.

    Sample i covers ``(ts[i-1], ts[i]]``. DCGM fields can trail one sample, so the averaged
    samples are those wholly inside the timed window except the first, and their activity must
    be steady; ``before`` is the last interval ending at or before the window's start; ``after``
    is the second interval starting at or after its end.

    Args:
        load: Output of :func:`parse_load`.
        samples: Output of :func:`device_samples`.
        kind: ``hmma`` or ``dmma``.
        dev: nvidia_gpu device field, for reasons.

    Returns:
        ``{status, reason, n, ...}``; status PASS (with ``per_sm_clock``, ``activity``,
        ``clock``, ``inside``), NOT_PRESENT (pipe idle under its load), FAIL or NOT_RUN.
    """
    n = int(load["n"])
    res: dict[str, Any] = {"status": "NOT_RUN", "n": n, "reason": ""}
    flops, calls, secs = int(load["flops"]), int(load["calls"]), float(load["elapsed_s"])
    if calls <= 0 or secs <= 0 or flops != calls * 2 * n ** 3:
        res.update(status="FAIL", reason=f"flops {flops} != calls {calls} x 2*n^3 (n={n}) "
                                         f"or no timed calls (elapsed {secs} s)")
        return res
    t0, t1 = float(load["start_epoch"]), float(load["end_epoch"])
    akey = ACTIVITY_KEY[kind]
    intervals = [(samples[i - 1][0], samples[i][0], samples[i][1]) for i in range(1, len(samples))]
    inside = [r for a, b, r in intervals if a >= t0 and b <= t1][1:]
    before = [r for a, b, r in intervals if b <= t0][-1:]
    after = [r for a, b, r in intervals if a >= t1][1:2]
    if len(inside) < MIN_INSIDE or not before or not after:
        res["reason"] = (f"{len(inside)} sample interval(s) inside the load window "
                         f"({t0:.1f}..{t1:.1f}), before={len(before)}, after={len(after)}: "
                         f"need {MIN_INSIDE} after the first plus idle samples each side; raise --seconds")
        return res
    acts, clocks = [], []
    for r in inside:
        for key, dst in ((akey, acts), (CLOCK_KEY, clocks)):
            v, why = _value(r, key, dev)
            if v is None:
                res["reason"] = why
                return res
            dst.append(v)
    for label, r in (("before", before[0]), ("after", after[0])):
        v, why = _value(r, akey, dev)
        if v is None:
            res["reason"] = why
            return res
        if v > IDLE_PCT:
            res.update(status="FAIL", reason=f"{akey} {v}% {label} the load (> {IDLE_PCT}%): "
                                             "other GPU work or a stale sample")
            return res
    if max(acts) - min(acts) > STEADY_PCT:
        res.update(status="FAIL", reason=f"{akey} not steady under the load ({acts} %, spread "
                                         f"> {STEADY_PCT} points)")
        return res
    act = sum(acts) / len(acts)
    clock = sum(clocks) / len(clocks)
    sms = int(load["sms"])
    res.update(activity=act, clock=clock, inside=len(inside), flop_s=flops / secs, sms=sms)
    if act <= IDLE_PCT:
        res.update(status="NOT_PRESENT", reason=f"{akey} {act:.1f}% under the load: this GEMM "
                                                "does not drive the pipe on this device")
        return res
    if clock <= 0 or sms <= 0:
        res.update(status="FAIL", reason=f"sm clock {clock} MHz / SMs {sms} not positive")
        return res
    res.update(status="PASS", per_sm_clock=flops / secs / (act / 100.0 * sms * clock * 1e6))
    return res


def combine(kind: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Kind verdict from its per-size results.

    Args:
        kind: ``hmma`` or ``dmma``.
        results: :func:`evaluate_load` results of the kind's sizes.

    Returns:
        ``{status, reason, value}``: PASS with the mean rate when every size passed and the
        spread is within ``AGREE``; NOT_PRESENT when every size left a DMMA pipe idle (an idle
        HMMA pipe under an FP16 GEMM is FAIL); otherwise FAIL or NOT_RUN.
    """
    stats = {r["status"] for r in results}
    if len(results) < 2:
        return {"status": "NOT_RUN", "reason": f"{len(results)} size(s) ran, need 2", "value": None}
    if "FAIL" in stats:
        return {"status": "FAIL", "reason": "a size failed", "value": None}
    if "NOT_RUN" in stats:
        return {"status": "NOT_RUN", "reason": "a size did not run", "value": None}
    if stats == {"NOT_PRESENT"}:
        if kind == "hmma":
            return {"status": "FAIL", "reason": "an FP16 GEMM left tensor_hmma_active idle",
                    "value": None}
        return {"status": "NOT_PRESENT", "reason": "pipe idle at every size", "value": None}
    if stats != {"PASS"}:
        return {"status": "FAIL", "reason": "sizes disagree on whether the pipe ran", "value": None}
    vals = [r["per_sm_clock"] for r in results]
    spread = (max(vals) - min(vals)) / max(vals)
    detail = ", ".join(f"n={r['n']} {r['per_sm_clock']:.1f}" for r in results)
    if spread > AGREE:
        return {"status": "FAIL", "value": None,
                "reason": f"sizes disagree by {100 * spread:.1f}% (> {100 * AGREE:.0f}%): {detail}"}
    return {"status": "PASS", "value": sum(vals) / len(vals),
            "reason": f"{detail}, spread {100 * spread:.1f}%"}


def table_value(v: float) -> int:
    """The nearest power of two when within ``AGREE`` of v (per-SM rates are), else round(v)."""
    p = 2 ** round(math.log2(v)) if v > 0 else 0
    return int(p) if p and abs(v - p) / p <= AGREE else round(v)


def pipe_index(header: str, kind: str) -> int:
    """Position of the kind's ``NVIDIA_GPU_PIPE_*`` in ``enum nvidia_gpu_pipe`` (the g_peaks column).

    Raises:
        ValueError: The enum or the member is missing from the header text.
    """
    m = re.search(r"enum nvidia_gpu_pipe\s*\{(.*?)\}", header, re.S)
    if not m:
        raise ValueError("no enum nvidia_gpu_pipe")
    names = re.findall(r"\b(NVIDIA_GPU_PIPE_\w+)", re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S))
    return names.index(PIPE_ENUM[kind])


def peaks_row(src: str, major: int, minor: int) -> tuple[int, list[int]] | None:
    """``(fp32_lanes, flops[])`` of the ``g_peaks`` row for major.minor, or None when absent."""
    for m in re.finditer(r"\{\s*(\d+),\s*(\d+),\s*(\d+),\s*\{([\d,\s]+)\}\s*\}", src):
        if (int(m.group(1)), int(m.group(2))) == (major, minor):
            return int(m.group(3)), [int(x) for x in m.group(4).split(",")]
    return None


def format_row(major: int, minor: int, lanes: int, flops: list[int]) -> str:
    """A ``g_peaks`` initializer row in the table's own layout."""
    return f"{{{major}, {minor}, {lanes}, {{{', '.join(str(f) for f in flops)}}}}},"


def evaluate_run(out_dir: str, src_dir: str, dev: str) -> tuple[list[str], str]:
    """Report lines and the verdict (OK, FAIL or NOT_RUN) of one calibration run directory.

    Args:
        out_dir: Run directory (``schema.txt``, ``samples/``, ``load_*.txt``).
        src_dir: Monitor ``src/`` holding ``nvidia_gpu_estimate.c`` / ``.h``.
        dev: nvidia_gpu device field of the calibrated GPU.

    Returns:
        (lines, verdict); the last line is ``GPU_TENSOR_CALIBRATE_<verdict>``.
    """
    lines: list[str] = []
    try:
        with open(os.path.join(out_dir, "schema.txt"), encoding="utf-8") as f:
            schema = parse_schema(f.read())
    except OSError as exc:
        return [f"NOT_RUN schema: {exc}", "GPU_TENSOR_CALIBRATE_NOT_RUN"], "NOT_RUN"
    texts = []
    for p in sorted(glob.glob(os.path.join(out_dir, "samples", "*.txt"))):
        with open(p, encoding="utf-8", errors="replace") as f:
            texts.append(f.read())
    samples = device_samples(texts, schema, dev)
    per_kind: dict[str, list[dict[str, Any]]] = {k: [] for k in ACTIVITY_KEY}
    shape = set()
    verdicts = []
    for p in sorted(glob.glob(os.path.join(out_dir, "load_*.txt"))):
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read()
        load = parse_load(text)
        name = os.path.basename(p)
        if load is None or load.get("kind") not in per_kind:
            tail = " | ".join(text.strip().splitlines()[-2:])
            lines.append(f"NOT_RUN load {name}: no result line ({tail or 'empty'})")
            verdicts.append("NOT_RUN")
            continue
        shape.add((load["cc"], load["sms"]))
        r = evaluate_load(load, samples, load["kind"], dev)
        per_kind[load["kind"]].append(r)
        if r["status"] in ("PASS", "NOT_PRESENT"):
            lines.append(f"{r['status']} load {load['kind']} n={r['n']} activity={r['activity']:.1f}% "
                         f"sm_clock={r['clock']:.0f}MHz sms={r['sms']} flop_s={r['flop_s']:.4e} "
                         + (f"per_sm_clock={r['per_sm_clock']:.1f} " if "per_sm_clock" in r else "")
                         + f"inside={r['inside']}" + (f" ({r['reason']})" if r["reason"] else ""))
        else:
            lines.append(f"{r['status']} load {load['kind']} n={r['n']}: {r['reason']}")
    if len(shape) > 1:
        lines.append(f"FAIL loads ran on different devices (cc, sms): {sorted(shape)}")
        verdicts.append("FAIL")
    kinds = {}
    for kind, results in per_kind.items():
        c = combine(kind, results)
        kinds[kind] = c
        verdicts.append(c["status"])
        value = f" per_sm_clock={c['value']:.1f} table={table_value(c['value'])}" if c["value"] else ""
        lines.append(f"{c['status']} {kind}{value} ({c['reason']})")
    verdict = "FAIL" if "FAIL" in verdicts else "NOT_RUN" if "NOT_RUN" in verdicts else "OK"
    if verdict != "FAIL" and len(shape) == 1:
        cc, _ = next(iter(shape))
        major, minor = (int(x) for x in cc.split("."))
        with open(os.path.join(src_dir, "nvidia_gpu_estimate.c"), encoding="utf-8") as f:
            row = peaks_row(f.read(), major, minor)
        with open(os.path.join(src_dir, "nvidia_gpu_estimate.h"), encoding="utf-8") as f:
            header = f.read()
        if row is None:
            lines.append(f"NOTE no g_peaks row for CC {cc} in nvidia_gpu_estimate.c; add one with "
                         "the measured tensor columns above")
        else:
            lanes, flops = row
            for kind, c in kinds.items():
                if c["status"] == "PASS":
                    flops[pipe_index(header, kind)] = table_value(c["value"])
                elif c["status"] == "NOT_PRESENT":
                    flops[pipe_index(header, kind)] = 0
                else:
                    lines.append(f"NOTE {kind} column keeps the current value (not measured)")
            lines.append("ROW " + format_row(major, minor, lanes, flops))
    lines.append(f"GPU_TENSOR_CALIBRATE_{verdict}")
    return lines, verdict


def main(argv: list[str]) -> int:
    """CLI: ``gpu_calibrate_eval OUT_DIR SRC_DIR DEVICE``.

    Returns:
        0 OK, 1 FAIL, 3 NOT_RUN, 2 on a usage error.
    """
    ap = argparse.ArgumentParser(prog="gpu_calibrate_eval")
    ap.add_argument("out_dir")
    ap.add_argument("src_dir")
    ap.add_argument("device")
    try:
        args = ap.parse_args(argv[1:])
    except SystemExit:
        return 2
    lines, verdict = evaluate_run(args.out_dir, args.src_dir, args.device)
    print("\n".join(lines))
    return {"OK": 0, "FAIL": 1}.get(verdict, 3)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
