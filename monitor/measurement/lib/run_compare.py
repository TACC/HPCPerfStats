"""Catalog what differs between overhead-profile runs that are meant to be compared per leg.

Campaign 2026-11-09 was not publishable: legs were 1200 s on one node and 1800 s on the others
(auto-fit), and each node ran its own site conf (buffer 48 h / 4096 / 6 h, different
``jobid_file``). This module turns that manual diff into a check:

* **must match** -- per-leg settings and the daemon conf (broker connection excepted). Any
  difference, or any non-empty ``fit_steps``, makes the comparison invalid.
* **intended** -- fleet build, OS/toolchain, hardware and the collector groups/legs that follow
  from it. Listed, never a failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Broker connection: necessarily per site, never part of the comparison.
CONF_PER_SITE = ("server", "queue", "user", "password")
SCHEDULE_KEYS = ("leg_seconds", "settle_seconds", "fwq_seconds", "blocks", "governors",
                 "off_legs")
INTENDED_BUILD = ("rpm_nvra", "capability_slug", "fleet", "shared_node_multi_jid",
                  "ib_mad_dlopen", "git_commit", "git_dirty")
INTENDED_HOST = ("kernel", "os", "nproc_online", "numa_nodes", "mem_gib", "gpus")


def parse_conf(text: str | None) -> dict[str, str]:
    """``key value`` lines of a daemon conf (comments and blanks dropped, last key wins)."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        parts = line.split(None, 1)
        if parts and not parts[0].startswith("#"):
            out[parts[0]] = parts[1].strip() if len(parts) > 1 else ""
    return out


def _os_name(release: str | None) -> str | None:
    for line in (release or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip('"')
    return None


def load_run(run_dir: Path) -> dict[str, Any]:
    """The comparable facts of one run directory (``raw/run.json`` + ``raw/provenance.json``)."""
    raw = Path(run_dir) / "raw"
    if not (raw / "run.json").is_file() or not (raw / "provenance.json").is_file():
        raise ValueError(f"{run_dir}: not a run directory (no raw/run.json + raw/provenance.json)")
    run = json.loads((raw / "run.json").read_text())
    prov = json.loads((raw / "provenance.json").read_text())
    build, host, sched = prov.get("build", {}), prov.get("host", {}), run.get("schedule", {})
    caps = build.get("build_capabilities") or {}
    conf = parse_conf(build.get("conf_redacted"))
    legs: dict[str, int] = {}
    for leg in sched.get("legs", []):
        legs[leg["kind"]] = legs.get(leg["kind"], 0) + 1
    qip = (build.get("rpm_qip") or {}).get("stdout") or ""
    nvr = {k.strip(): v.strip() for k, _, v in (ln.partition(":") for ln in qip.splitlines())}
    return {
        "name": host.get("short") or Path(run_dir).name,
        "schedule": {k: sched.get(k) for k in SCHEDULE_KEYS},
        "fit_steps": sched.get("fit_steps") or [],
        "conf": {k: v for k, v in conf.items() if k not in CONF_PER_SITE},
        "build": {"rpm_nvra": "-".join(filter(None, (nvr.get("Name"), nvr.get("Version"),
                                                     nvr.get("Release")))) or None,
                  "capability_slug": caps.get("capability_slug"), "fleet": caps.get("fleet"),
                  "shared_node_multi_jid": (caps.get("configure") or {}).get(
                      "shared_node_multi_jid"),
                  "ib_mad_dlopen": (caps.get("configure") or {}).get("ib_mad_dlopen"),
                  "git_commit": ((build.get("git") or {}).get("commit") or "")[:12] or None,
                  "git_dirty": (build.get("git") or {}).get("dirty")},
        "host": {"kernel": host.get("kernel"), "os": _os_name(host.get("os_release")),
                 "nproc_online": host.get("nproc_online"), "numa_nodes": host.get("numa_nodes"),
                 "mem_gib": round(host["mem_total_kib"] / 2 ** 20) if host.get(
                     "mem_total_kib") else None,
                 "gpus": len([g for g in host.get("gpus") or [] if "3D controller" in g])},
        "groups": sorted(sched.get("groups") or []),
        "variants": sorted(sched.get("variants") or []),
        "legs": legs,
        "flags": run.get("flags") or {},
    }


def _rows(runs: list[dict[str, Any]], section: str, keys: list[str]) -> list[tuple[str, list]]:
    return [(k, [r[section].get(k) for r in runs]) for k in keys]


def compare(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Split facts into must-match rows (with ``same``) and intended rows."""
    conf_keys = sorted(set().union(*(r["conf"] for r in runs)))
    must = [(f"schedule.{k}", v) for k, v in _rows(runs, "schedule", list(SCHEDULE_KEYS))]
    must += [(f"conf.{k}", v) for k, v in _rows(runs, "conf", conf_keys)]
    must.append(("fit_steps", [[s.get("step") for s in r["fit_steps"]] for r in runs]))
    must_rows = []
    for key, vals in must:
        same = len({json.dumps(v, sort_keys=True) for v in vals}) == 1
        if key == "fit_steps":
            same = all(v == [] for v in vals)
        must_rows.append({"key": key, "values": vals, "same": same})
    intended = [(f"build.{k}", v) for k, v in _rows(runs, "build", list(INTENDED_BUILD))]
    intended += [(f"host.{k}", v) for k, v in _rows(runs, "host", list(INTENDED_HOST))]
    intended += [("groups", [",".join(r["groups"]) for r in runs]),
                 ("variants", [",".join(r["variants"]) or "-" for r in runs]),
                 ("legs", [" ".join(f"{k}x{n}" for k, n in sorted(r["legs"].items()))
                           for r in runs]),
                 ("flags", [",".join(sorted(r["flags"])) or "-" for r in runs])]
    return {"names": [r["name"] for r in runs], "must": must_rows,
            "intended": [{"key": k, "values": v} for k, v in intended],
            "ok": all(row["same"] for row in must_rows)}


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    text = value if isinstance(value, str) else json.dumps(value)
    return text.replace("|", "\\|")


def to_markdown(result: dict[str, Any]) -> str:
    names = result["names"]
    head = "| Item | " + " | ".join(names) + " |"
    sep = "|" + " --- |" * (len(names) + 1)
    verdict = ("**COMPARABLE** -- every must-match row is identical and no run shortened or "
               "dropped legs." if result["ok"] else
               "**NOT COMPARABLE** -- rows marked DIFF must be identical across runs.")
    lines = ["# Run comparison (per-leg parity)", "", f"Verdict: {verdict}",
             "", "## Must match (per-leg settings and daemon conf; broker connection excluded)",
             "", head.replace("| Item |", "| Item | Status |"),
             sep + " --- |"]
    for row in result["must"]:
        status = "same" if row["same"] else "**DIFF**"
        lines.append(f"| {row['key']} | {status} | " + " | ".join(_cell(v) for v in row["values"])
                     + " |")
    lines += ["", "## Intended differences (fleet build, OS, hardware; recorded, not a failure)",
              "", head, sep]
    for row in result["intended"]:
        lines.append(f"| {row['key']} | " + " | ".join(_cell(v) for v in row["values"]) + " |")
    lines += ["", "Per-leg data for every run: `raw/legs/<leg_id>/` (summary.json, samples.jsonl, "
              "journal.txt, perf.csv); leg status and contamination in `profile.json` -> `legs`.",
              ""]
    return "\n".join(lines)
