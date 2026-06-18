# Monitor emitted variable naming scheme

Canonical rules for **`st_name`** (collector type) and **event keys** emitted by
`hpcperfstatsd`. Machine-readable renames: [`monitor_variable_rename_map.yaml`](monitor_variable_rename_map.yaml).

Generated: 2026-06-17 (also run `docs/generate_monitor_rename_table.py` after map edits).

## Level 1 — collector (`st_name`)

Pattern: `{origin}_{domain}[{variant}]` — lowercase snake only.

| Segment | Examples |
|---------|----------|
| origin | `host`, `intel`, `amd`, `arm`, `nvidia`, `lustre` |
| domain | `cpu`, `mem`, `pmc`, `uncore_imc`, `rapl`, `gpu`, `ib`, `roofline_peak` |
| variant | `gpr8`, `snb`, `skx` (generation or counter width) |

Architecture and CPU generation belong in the **type name**, not in event keys.

## Level 2 — event keys

Pattern: `{resource}_{signal}[{qualifier}]` — lowercase snake, ASCII.

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

Future ISAs: add `{vendor}_{isa}_{domain}[{variant}]`; reuse Level-2 keys for the same physical quantity.

## Enforcement

- Agent rule: `.cursor/rules/monitor-emitted-variable-naming.mdc`
- CI lint: `monitor/scripts/check_emitted_variable_names.py` (via `make check`)

See [`artifacts/monitor-variable-rename-table.md`](../artifacts/monitor-variable-rename-table.md) for the full old→new migration table.
