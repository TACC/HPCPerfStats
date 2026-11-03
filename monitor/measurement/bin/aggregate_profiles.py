#!/usr/bin/env python3
"""Merge many profile.json files into one cross-architecture CSV, Markdown and LaTeX table.

Profiles flagged ``not_idle`` (smoke runs) are excluded and listed on stderr.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.profile_report import (
    CSV_KEYS,
    HEADLINE_METRICS,
    NOT_MEASURED,
)

LABELS = {k: (label, unit) for k, label, unit in HEADLINE_METRICS}
_TEX = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
        "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def tex_escape(text: str) -> str:
    return "".join(_TEX.get(ch, ch) for ch in str(text))


def cell(slot: dict) -> str:
    if slot.get("status") == NOT_MEASURED:
        return "n/m"
    v = slot["value"]
    txt = f"{v:.3g}" if isinstance(v, float) else str(v)
    if slot.get("ci95"):
        lo, hi = slot["ci95"]
        txt += f" ({lo:.3g}–{hi:.3g})"
    return txt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profiles", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for path in args.profiles:
        prof = json.loads(path.read_text())
        host = prof.get("host") or {}
        if prof.get("not_idle"):
            print(f"excluded {path} ({host.get('short')}): not_idle {prof['not_idle']}",
                  file=sys.stderr)
            continue
        rows.append((host, prof["metrics"]))
    args.out.mkdir(parents=True, exist_ok=True)
    heads = ["host", "arch", "cpu_model", *CSV_KEYS]
    with (args.out / "profiles.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(heads)
        for host, m in rows:
            w.writerow([host.get("short"), host.get("arch"), host.get("cpu_model"),
                        *("" if m[k].get("status") == NOT_MEASURED else m[k]["value"] for k in CSV_KEYS)])
    cols = [f"{LABELS[k][0]} ({LABELS[k][1]})" for k in CSV_KEYS]
    md = ["| Host | Arch | CPU | " + " | ".join(cols) + " |",
          "| " + " | ".join(["---"] * (3 + len(cols))) + " |"]
    for host, m in rows:
        md.append(f"| {host.get('short')} | {host.get('arch')} | {host.get('cpu_model')} | "
                  + " | ".join(cell(m[k]) for k in CSV_KEYS) + " |")
    (args.out / "profiles.md").write_text("\n".join(md) + "\n")
    tex = [r"\begin{tabular}{lll" + "r" * len(CSV_KEYS) + "}", r"\toprule",
           " & ".join(["Host", "Arch", "CPU"] + [tex_escape(c) for c in cols]) + r" \\", r"\midrule"]
    for host, m in rows:
        tex.append(" & ".join(tex_escape(x) for x in (host.get("short"), host.get("arch"),
                                                      host.get("cpu_model"),
                                                      *(cell(m[k]) for k in CSV_KEYS))) + r" \\")
    tex += [r"\bottomrule", r"\end{tabular}"]
    (args.out / "profiles.tex").write_text("\n".join(tex) + "\n")
    for name in ("profiles.csv", "profiles.md", "profiles.tex"):
        print(args.out / name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
