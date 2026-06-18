#!/usr/bin/env python3
"""Generate MONITOR_NAMING_SCHEME.md and monitor-variable-rename-table.md from YAML.

Run from HPCPerfStats/:
  .venv/bin/python docs/generate_monitor_rename_table.py
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

REPO = Path(__file__).resolve().parents[1]
YAML_PATH = REPO / "docs" / "monitor_variable_rename_map.yaml"
SCHEME_OUT = REPO / "docs" / "MONITOR_NAMING_SCHEME.md"
TABLE_OUT = REPO / "artifacts" / "monitor-variable-rename-table.md"

DOWNSTREAM = [
    "hpcperfstats/dbload/lib/monitor_naming/canonical.py",
    "hpcperfstats/dbload/lib/monitor_naming/legacy.py",
    "hpcperfstats/dbload/lib/monitor_naming/resolve.py",
    "hpcperfstats/dbload/lib/sync_timedb_parsing.py",
    "hpcperfstats/dbload/lib/sync_timedb_parsing_legacy.py",
    "hpcperfstats/analysis/metrics/lib/metrics.py",
    "tests/pipeline_e2e/monitor_payloads.py",
    "hpcperfstats/site/frontend/src/utils/variableMetadataMonitorEvents.js",
    "hpcperfstats/site/frontend/src/utils/variableMetadataMonitorEventsLegacy.js",
    "docs/MONITOR_VARIABLES.md",
]


def load_map() -> dict:
    if yaml is None:
        raise SystemExit("PyYAML required: .venv/bin/pip install pyyaml")
    with YAML_PATH.open(encoding="utf-8") as fd:
        return yaml.safe_load(fd)


def write_scheme(data: dict) -> None:
    SCHEME_OUT.write_text(
        f"""# Monitor emitted variable naming scheme

Canonical rules for **`st_name`** (collector type) and **event keys** emitted by
`hpcperfstatsd`. Machine-readable renames: [`monitor_variable_rename_map.yaml`](monitor_variable_rename_map.yaml).

Generated: {date.today().isoformat()} (also run `docs/generate_monitor_rename_table.py` after map edits).

## Level 1 — collector (`st_name`)

Pattern: `{{origin}}_{{domain}}[{{variant}}]` — lowercase snake only.

| Segment | Examples |
|---------|----------|
| origin | `host`, `intel`, `amd`, `arm`, `nvidia`, `lustre` |
| domain | `cpu`, `mem`, `pmc`, `uncore_imc`, `rapl`, `gpu`, `ib`, `roofline_peak` |
| variant | `gpr8`, `snb`, `skx` (generation or counter width) |

Architecture and CPU generation belong in the **type name**, not in event keys.

## Level 2 — event keys

Pattern: `{{resource}}_{{signal}}[{{qualifier}}]` — lowercase snake, ASCII.

| Category | Rule |
|----------|------|
| Kernel mirrors | Map `/proc`/`/sys` field to snake_case at emit time (`MemTotal` → `mem_total`) |
| Portable semantics | Reuse cross-vendor keys: `instr_retired`, `aperf`, `mperf`, `dram_cas_reads`, `pkg_energy` |
| Vendor PMU | Lowercase official SDM/PPR mnemonic, or prefer portable name |
| Energy | `pkg_energy`, `dram_energy` (units in schema `U=J`) |
| GPU | `gpu_*` prefix; `gpu_mem_*` for framebuffer metrics |
| Fabric | snake_case (`port_xmit_data`, not PascalCase) |

**Forbidden in emission:** `CTL*`, `CTR*`, `FIXED_CTR*`, `MSR_*` register literals, hex control
tokens (`0xD8`…), schema `,C` control columns.

## Adding a collector

1. Choose `st_name` using Level 1; check existing types for reusable event keys.
2. Define `KEYS` / `stats_set` with **identical** literals; register in `stats_registry.c`.
3. For kernel-driven keys, use `host_key_alias_emit()` or an equivalent map.
4. Add or extend a schema contract test under `monitor/tests/`.
5. Append new semantic keys to `monitor_variable_rename_map.yaml` with a one-line rationale.
6. Run `monitor/scripts/check_emitted_variable_names.py` and `make check`.

Future ISAs: add `{{vendor}}_{{isa}}_{{domain}}[{{variant}}]`; reuse Level-2 keys for the same physical quantity.

## Enforcement

- Agent rule: `.cursor/rules/monitor-emitted-variable-naming.mdc`
- CI lint: `monitor/scripts/check_emitted_variable_names.py` (via `make check`)

See [`artifacts/monitor-variable-rename-table.md`](../artifacts/monitor-variable-rename-table.md) for the full old→new migration table.
""",
        encoding="utf-8",
    )


def write_table(data: dict) -> None:
    types = data.get("types") or {}
    events = data.get("events") or {}
    removed = data.get("removed_legacy") or []
    lines = [
        "# Monitor variable rename table",
        "",
        f"Generated {date.today().isoformat()} from `docs/monitor_variable_rename_map.yaml`.",
        "",
        "**Clean break:** no ingest aliases. Downstream must adopt new names.",
        "",
        "## Downstream impact (document-only)",
        "",
    ]
    for path in DOWNSTREAM:
        lines.append(f"- `{path}`")
    lines.extend(["", "## Type renames (`st_name`)", ""])
    for old, new in sorted(types.items()):
        lines.append(f"- `{old}` → `{new}`")
    lines.extend(["", "## Event renames (global)", ""])
    for old, new in sorted(events.items()):
        if old.startswith("'") or new.startswith("'"):
            continue
        if old in ("C", "E", "Z", "mJ", "PRIu64", "NONE", "NULL", "TRUE", "FALSE", "NO", "ON", "YES"):
            continue
        if old.startswith("HPCPERFSTATS_") or old.startswith("nvml") or old.startswith("Gpa"):
            continue
        lines.append(f"- `{old}` → `{new}`")
    lines.extend(["", "## Host mem aliases (kernel → emit)", ""])
    for old, new in sorted((data.get("host_mem_aliases") or {}).items()):
        lines.append(f"- `{old}` → `{new}`")
    lines.extend(["", "## Host proc aliases (kernel → emit)", ""])
    for old, new in sorted((data.get("host_proc_aliases") or {}).items()):
        lines.append(f"- `{old}` → `{new}`")
    lines.extend(["", "## Removed legacy symbols (never re-emit)", ""])
    for sym in removed:
        lines.append(f"- `{sym}`")
    lines.extend([
        "",
        "## Semantic replacements for removed register-shaped keys",
        "",
        "- `FIXED_CTR0` → `instr_retired`",
        "- `FIXED_CTR1` → `aperf`",
        "- `FIXED_CTR2` → `mperf`",
        "- `CTLn`/`CTRn` pairs → logical PMU names at emit time (decode path retired for new archives)",
        "",
    ])
    TABLE_OUT.parent.mkdir(parents=True, exist_ok=True)
    TABLE_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    data = load_map()
    write_scheme(data)
    write_table(data)
    print(f"Wrote {SCHEME_OUT.relative_to(REPO)}")
    print(f"Wrote {TABLE_OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
