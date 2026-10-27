# Codon FT parse feasibility baseline (updated 2026-10-21)

Spike scope: bounded **feed-only** parse kernel under `experiments/codon/`,
oracle parity vs `IncrementalStatsParser`, microbench **Codon-native vs
`/opt/python3.14t`** inside the pipeline image. **No** production `dbload/`
edits, **no** Docker/supervisord ABI changes.

## Toolchain

| Item | Result |
|------|--------|
| Codon version | **0.18.0** at `/data/user/$USER/codon/` (mounted into container) |
| Install log | `test_runs/codon_install_20261007.log` |
| Host CPython | `/data/HPCPerfStats/.venv/bin/python3` → **3.14.10** |
| Host `/opt/python3.14t` | **Unavailable** on spike host (use pipeline image) |
| Container linker | **Clang 19** via `apt-get install clang` + PATH shim `g++` → `clang++` (Codon hardcodes argv `g++`; no `g++` package) |
| `@python` shims in native kernel | **0** |

## Compile inventory (host, 2026-10-07)

Log: `test_runs/codon_inventory_20261007T172553.log`

Host lacked `g++`/`clang++`, so tiers 0–1 failed at link. Multi-module
`parse_kernel` + `pathlib` still fail under Codon. **Resolution:** single-file
`codon_native_e6_bench.py` + container Clang shim.

## Parity (correctness)

| Test | Result |
|------|--------|
| `test_codon_parse_kernel_parity.py` | **3/3 pass** (CPython `FeedKernel`) |
| `test_codon_native_e6_fingerprint.py` | **pass** (native kernel under CPython == oracle) |
| Codon-native fingerprint (container) | `stats_rows=1200 proc_rows=1 stats_value_sum=263400 proc_vm_peak_sum=499` (matches oracle) |

## Performance (microbench, pipeline image, 2026-10-21)

Log: `test_runs/codon_vs_314t_20261021T094310.log`

Runner: `experiments/codon/run_codon_vs_314t_container.sh` (Clang shim, not g++).

| Arm | Time (80 reps × 400 samples) | Notes |
|-----|------------------------------|-------|
| **Codon-native** `codon_native_e6_bench.py` | **0.187 s** | `codon run -release`, linked with Clang 19 |
| `IncrementalStatsParser` (**3.14t** `/opt/python3.14t`) | **0.881 s** | Production feed path |
| CPython `FeedKernel` on 3.14t | 0.711 s | Same interpreter as FT baseline |
| **Codon vs 3.14t Incremental** | **4.72×** | Plan gate ≥ **1.25×** → **met** |

Earlier host-only CPython-vs-CPython (~1.28×) is **not** the plan gate; the
gate is Codon-native vs 3.14t.

## Ranked remaining blockers (integration, not microbench)

1. **Single-file / typed rewrite** — production `parse_kernel.py` / multi-module
   layout still not Codon-clean; only `codon_native_e6_bench.py` is the proven
   native unit.
2. **Codon argv `g++`** — must shim or install a C++ driver; prefer Clang to
   match pipeline bake (`python-image-interpreter-contract.mdc`).
3. **Collapse / pandas** — real ingest still uses `_collapse_stats_with_deltas`
   in CPython; not in this spike.
4. **Daemon wiring** — no supervisord / image change yet; extension module or
   subprocess IPC design TBD.

## Recommendation

**Microbench gate passed** (Codon-native ≥ 1.25× vs 3.14t on E6 feed-only).

**Do not** wire into `sync_timedb` / `listend` yet until:

1. Broader corpus parity beyond E6 (real archive excerpts).
2. A packaging story (Codon shared library / extension loaded by 3.14t, or
   bounded subprocess) that preserves FT daemon concurrency.
3. Collapse-path strategy (stay in CPython vs Codon-NumPy / `@python`).

Keep production `sync_timedb_parsing.py` unchanged until that design lands.

## How to reproduce

```bash
# From HPCPerfStats/ git checkout:
./experiments/codon/run_codon_vs_314t_container.sh
# Logs under workspace test_runs/codon_vs_314t_*.log
```
