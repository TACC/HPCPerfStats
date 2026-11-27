# Monitor naming scheme

This page explains how the monitor names its types (`st_name`) and keys, and why, for developers who add a collector or read `host_data`.

Every name the monitor emits is lowercase snake case ASCII.
A type name says where a counter comes from and which hardware generation it reads; a key name says what quantity it measures.
Keeping those two jobs apart lets one key mean the same quantity on every backend, so analysis code reads `instr_retired` or `pkg_energy` without a per-vendor branch.

## Type names

A type name has the pattern `{origin}_{domain}[_{variant}]`.

| Segment | Meaning                                     | Examples                                                                  |
| ------- | ------------------------------------------- | ------------------------------------------------------------------------- |
| origin  | Who provides the counter                    | `host`, `intel`, `amd`, `arm`, `nvidia`, `lustre`, `beegfs`               |
| domain  | What subsystem it reads, ISA first for PMUs | `cpu`, `mem`, `ib`, `gpu`, `x86_rapl`, `x86_uncore_imc`, `aarch64_imc`    |
| variant | Hardware generation, when keys differ       | `skx`, `icx`, `spr`, `emr`, `gnr`, `srf` (Intel), `rome` to `turin` (AMD) |

Architecture and generation go in the type, never in a key.
`intel_x86_uncore_imc_spr` and `intel_x86_uncore_imc_icx` both emit `dram_cas_reads`; the type tells the reader which counter programming produced it.
A new ISA follows the same pattern (`{vendor}_{isa}_{domain}[_{variant}]`) and reuses the existing keys for the same quantity.

## Key names

A key has the pattern `{resource}_{signal}[_{qualifier}]`.

| Kind               | Rule                                                                               | Examples                                            |
| ------------------ | ---------------------------------------------------------------------------------- | --------------------------------------------------- |
| Kernel mirrors     | Map the `/proc` or `/sys` field to snake case at emit time (`host_key_alias_emit`) | `MemTotal` -> `mem_total`, `VmRSS` -> `vm_rss`      |
| Portable semantics | Reuse one key for one quantity across vendors and backends                         | `instr_retired`, `aperf`, `mperf`, `dram_cas_reads` |
| Vendor PMU         | Lowercase official mnemonic when no portable name exists                           | `fp_arith_inst_retired_scalar_double`               |
| Energy             | `*_energy` running totals; the unit sits in the schema (`U=mJ`), not in the key    | `pkg_energy`, `dram_energy`                         |
| GPU                | `gpu_` prefix; `gpu_mem_` for framebuffer and memory metrics                       | `gpu_util`, `gpu_mem_used_mb`                       |
| Fabric             | Snake case of the counter name, never PascalCase                                   | `port_xmit_data`                                    |

Units, counter width and tier live in the schema options (`U=`, `W=`, `R=S`), so a key name stays stable when its unit changes.
The [monitor metrics reference](../../monitor/docs/METRICS_REFERENCE.md) lists every key with those options.

## Names the monitor never emits

Register-shaped names are forbidden in emission: `CTL*`, `CTR*`, `FIXED_CTR*`, `MSR_*`, hex event codes such as `0xD8`, and schema `,C` control columns.
They describe how a counter was programmed, not what it counts, and their meaning changes between generations.
Older archives carry them; ingest decodes those schemas through the legacy path (`resolve.schema_needs_legacy_hardware_decode`, `sync_timedb_parsing_legacy.py`).

Strings that only look like keys keep their spelling: `HPCPERFSTATS_*` environment variables, `dlsym` symbol names, CPUID vendor strings and the `cpuN` prefix in `/proc/stat`.

## Renames and legacy names

The monitor emits only canonical names, but `host_data` and the archives hold every older name.
The rename map `hpcperfstats/dbload/lib/monitor_naming/monitor_variable_rename_map.yaml` records each legacy-to-canonical mapping, and analysis probes the canonical name first, then the legacy ones.
A consumer-visible change to a type or key therefore adds a map entry in the same change: `types:` for a type, `events:` for a global key, `type_events:` for a key that collides with another type's key, and `removed_legacy` or `removed_legacy_events` for retired names.
[Monitor naming tables](../reference/monitor-naming-tables.md) renders the whole map.

## Where the rules are enforced

- `monitor/scripts/check_emitted_variable_names.py` runs in `make check` and fails on legacy type names, register-shaped keys and keys that are not lowercase snake case.
- `monitor/cursor-rules/monitor-emitted-variable-naming.mdc` lists the steps for a new collector: shared `KEYS` header, matching `stats_set` literals, `stats_registry.c`, a schema contract test, the rename map entry and the lint run.
- `monitor/cursor-rules/monitor-consumer-schema-migration.mdc` covers breaking output changes and the coordinated consumer rollout.

## Alternatives and trade-offs

- Register names in keys (`CTR0`) need no mapping in the monitor, but every reader must know each generation's counter programming; semantic keys move that knowledge into the monitor, once.
- Vendor names in keys (`intel_instr_retired`) would make cross-vendor metrics branch on the key; putting the vendor in the type keeps one key per quantity.
- Renaming in place would break history; the rename map keeps old rows readable at the cost of probing two names per read.

## Related

- [Monitor metrics reference](../../monitor/docs/METRICS_REFERENCE.md)
- [Monitor naming tables](../reference/monitor-naming-tables.md)
- [Monitor variables consumer map](../reference/monitor-variables-consumer-map.md)
- [Analysis architectures](../reference/analysis-architectures.md)
- [Raw data format](../../monitor/docs/RAW_DATA_FORMAT.md)
