# Run the monitor correctness campaign

This page shows monitor developers and node operators how to prove, on a real compute node, that a DEBUG build of `hpcperfstatsd` emits correct messages and counters.
Use it when you validate a new build on each CPU class (Intel skx / icx / spr / spr-max / pvc-host / h100-host, AMD Milan / Genoa / Turin, Grace GB200 / GH200) or check one collector after a change.

## Prerequisites

- A DEBUG build: the debug RPM (`./scripts/prepare_rpmbuild_dirs.sh --debug-build`, see [Build the RPM](build-the-rpm.md)) or `./scripts/build_static_bundle.sh --enable-debug`.
  Only DEBUG builds write the `/dev/shm/hpcperfstatsd-debug` mirror (`schema`, `fast`, `full`) that every check reads.
- Root on an otherwise idle compute node, and the monitor checkout on that node.
- A C compiler with OpenMP for the canary loads (`module load gcc` on TACC when `cc` lacks it).
- The repo `.venv` for the Python validators.

The wire format the checks validate (line kinds, the value sentinel catalog, tier markers) is in [RAW_DATA_FORMAT.md](../RAW_DATA_FORMAT.md).

## Run the per-node campaign

`./scripts/rpm_debug_shm_verify.sh` is the one command of the campaign.
It runs, in order: optional build, install and soak of the DEBUG RPM (`preflight`), validation of the `/dev/shm` messages, the counter-accuracy layer, the wired canary checks, and last the live fault injection, which restarts the daemon.
Nothing reads the terminal, every wait and external command is bounded, and loads stop on every exit including Ctrl-C.

To run it on one node:

1. From `HPCPerfStats/monitor/`, as root, build the debug RPM and run the verify in one line (`prepare_rpmbuild_dirs.sh --debug-build` prints it):

   ```bash
   rpmbuild -ba ... hpc_debug_build 1 ... \
     && ./scripts/rpm_debug_shm_verify.sh
   ```

   Or let the script build: `RPM_BUILD=1 ./scripts/rpm_debug_shm_verify.sh`.
   No exports are needed.

2. Optional: to re-validate an installed build only, run `SKIP_INSTALL=1 ./scripts/rpm_debug_shm_verify.sh`.

3. Read the result: the run exits non-zero when any check FAILed, and writes the campaign JSON (see [Read the campaign summary JSON](#read-the-campaign-summary-json)).

The soak after install runs about one `full` interval of `stress-ng --cpu 0`, then waits about `sample_freq_slow` for a second full snapshot.
The debug RPM installs `sample_freq 30` and `sample_freq_slow 60` in `/etc/hpcperfstats/hpcperfstats.conf`; the validator derives its cross-sample wait bounds from that file.
`WAIT_SHM_SECONDS` (default 300) bounds the wait for the mirror files.

### Run it on a campaign node

`scripts/campaign_verify_node.sh ls6|gb200` (root, any cwd) is the per-node operator command, so nobody pastes a multi-KB block: pastes over 4 KB lose lines on cluster nodes.

It does these steps:

1. Writes `site/hpcperfstats.site.conf` from the packaged conf when missing (key names printed, never values).
2. Removes the other clusters' fleet markers, resets the packaged conf and spec, and runs `git pull --ff-only`.
3. Checks the checkout gates, then runs the debug RPM build and `rpm_debug_shm_verify.sh` (logs in `/tmp/hps-cc-<host>/`, reports in `../test_runs/monitor/`).

`ls6` adapts to the node: Genoa (`lscpu` `EPYC 9xxx`) adds the DF channel step and needs the Genoa DF commit, and the GPU field-response step (four `dcgmproftester` loads) runs only when `nvidia-smi -L` lists a GPU.
`gb200` needs the CC 10.0 peak row, turns the four standalone checks off, and runs one tensor load (1004).
The debug RPM is erased only when every step returned 0; otherwise a `== KEEP debug RPM: ...` line names the step rc values.
Tests: `tests/test_campaign_verify_node.py`.

## Validate the shm messages by hand

To run the validators without the campaign script, from `HPCPerfStats/monitor/` with the DEBUG daemon running:

1. Build and write the capabilities file:

   ```bash
   ./scripts/build_static_bundle.sh --enable-debug
   make -C .build-static capabilities
   ```

2. Start `hpcperfstatsd` and wait until `schema`, `fast` and `full` have updated under `/dev/shm/hpcperfstatsd-debug`.

3. Build the expectations manifest and validate:

   ```bash
   CAPS=.build-static/monitor-build-capabilities.json
   SHM=/dev/shm/hpcperfstatsd-debug
   SLUG=$(python3 -c "import json,sys; \
     print(json.load(open(sys.argv[1]))['capability_slug'])" "$CAPS")
   EXP=".build-static/expectations_${SLUG}.json"
   python3 scripts/build_message_expectations.py \
     --capabilities "$CAPS" --shm-dir "$SHM" \
     --enable-slow-tier 1 --out "$EXP"
   python3 scripts/validate_shm_messages.py \
     --capabilities "$CAPS" --manifest "$EXP" \
     --shm-dir "$SHM" --live-spot-check \
     --report "../test_runs/monitor/validate_${SLUG}_$(date +%F).txt"
   ```

On Stampede3, prefer `./scripts/validate_stampede3_profile.sh --profile <PROFILE>` (one of skx, clx, icx, spr, h100, pvc, amd-rtx).
It adds the `__<profile>` suffix to expectations and reports and checks the queue type-presence contract (h100 requires `nvidia_gpu`, `host_ib` and `host_opa`).

`<PROFILE>` is the Stampede3 queue profile name.

### Capability slug

`monitor-build-capabilities.json` includes `capability_slug`: the compile flags plus `slowtier0` or `slowtier1`.
Stampede3 fleet DEBUG builds add `ibdyn`, `opadyn` and `intelgpu` when those macros are compiled in, plus JSON `fleet: "stampede3"` when the fleet signature matches.
Sysfs `host_opa` is always built; the `opa` and `opadyn` tokens reflect the MAD compile flags.
The same slug is compiled in (`src/monitor_capability_slug.h` via `BUILT_SOURCES` and `emit_build_capabilities.py`) and emitted on the `$` banner as `$build {capability_slug}` after `$uptime`.
Golden fixtures and expectations use the slug in their names (`tests/expected/shm_*_<slug>.txt`, `expectations_<slug>.json`, optional `__<profile>` suffix).
CI runs `tests/test_shm_message_correctness.sh`: the synthetic fixture always, live slug goldens when present, exit 77 (skip) otherwise.

### Validation layers

- Structure: schema keys, row width, tier markers, unsigned values or catalog sentinels (`scripts/lib/row_validate.py`), manifest device IDs, the listend host contract.
- Plausibility and live `/proc` / `/sys` spot checks: strict by default on debug verify.
- Cross-sample: cadence and `E`-counter monotonicity.
  The live run injects brief CPU and `/tmp` I/O (and a Lustre touch when a mount is present) so a flat counter means stuck or unused, not a quiet node; `--no-cross-sample-stimulus` turns that off.
  Sparse error or idle counters flat at 0 are silenced or aggregated; must-move canaries emit one WARN per stuck class.
- Golden diff (opt-in): copy the shm files to `tests/expected/shm_{schema,fast,full}_<slug>[__<profile>].txt` and set `GOLDEN_DIR=auto` or `GOLDEN_CHECK=1`, or `GOLDEN_DIR=/path` (with `--profile` for profile-scoped goldens).

### Sentinel layers

These layers check the value sentinels described in [RAW_DATA_FORMAT.md](../RAW_DATA_FORMAT.md); `emit_value_sentinels 1` (the default in `hpcperfstats.conf`) must be on.

- The expectations manifest carries `allowed_sentinels` and `expect_not_built` per type and key: a code outside it FAILs, and a key the build cannot read that emits a value FAILs.
- `NOT_PRESENT` is checked against `/proc/vmstat`, IB / OPA sysfs counters and the hwmon power meters; `nvidia_gpu` reports SKIP (no read-only DCGM field probe).
- A key `NOT_UPDATED` in two consecutive captures FAILs with its collector site.
- Independent read: a live read of the source (`/proc/stat`, IB / OPA counters, `/sys/block/<dev>/stat`) between two captures must lie between the emitted values, and a gauge (node `meminfo`, hwmon power) emitted 0 in both captures while its source is non-zero FAILs.
- Consumer parse: the captures go through the consumer parser (`scripts/consumer_parse_check.py`, repo `.venv`).
- `cross_sample_validate.py` skips chain-breaking gaps, FAILs a bridgeable gap whose next value dropped, and prints `NOTE sentinel` counts per code.
  Both CPU backends (`x86perf` and `arm`) bridge `host_cpu_hw` the same way, so a `host_cpu_hw` bridge violation FAILs on every slug.
- A capture without the `$sentinels` banner reports the sentinel layers as `SKIP`, never `PASS`, and any sentinel token in it FAILs.

## Check counter accuracy

`canary_counter_check.sh --accuracy` (every architecture) is the counter-accuracy layer (`COUNTER_CHECK`, default 1).
It compares the DEBUG daemon's own numbers from the `fast` mirror with loads whose totals are exact (`scripts/canary_gemm_load.sh`).
Each load runs between two fresh `fast` samples, so the daemon delta covers all of it.
`scripts/lib/counter_accuracy.py` holds the payload parsing, the tolerance table and the verdicts.

| Check                        | Load                                                                    | Daemon counter                                                                                                                                                                                                                             | Tolerance                                                  |
| ---------------------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------- |
| `mem_read`                   | `--kernel read` pinned (`taskset`) to socket-0 CPUs, `total_bytes_read` | `dram_cas_reads` x 64 B (+ SPR / EMR `hbm_cas_reads` x 32 B) per IMC / HBM row; AMD DF `dram_data_beats` (Zen2 / Zen3, read + write) or `dram_read_beats` (Zen4 / Zen5) x 64 B over every `chan<N>` row; Grace SCF `dram_cas_reads` x 64 B | 0.75-1.5                                                   |
| `mem_write`                  | `--kernel write` (`total_bytes_written`)                                | `dram_cas_writes` (+ `hbm_cas_writes`); AMD Zen4 / Zen5 `dram_write_beats` x 64 B; NOT_RUN on Zen2 / Zen3 (one combined event)                                                                                                             | 0.75-1.5                                                   |
| `mem_socket0`                | the read load                                                           | Grace `scf0` share of all SCF bytes; x86 NOT_RUN (each uncore row sums every socket)                                                                                                                                                       | >= 0.75                                                    |
| `flops_dp` / `flops_sp`      | dp / sp GEMM, `done total_flops`                                        | Intel `fp_arith_inst_retired_*` lane-weighted (scalar 1, 128b 2/4, 256b 4/8, 512b 8/16 dp/sp; FMA counts twice); AMD `fp_ops_retired`, Zen5 `amd_fp_dp_ops` (sp = total - dp; Zen3 / Zen4 total only); Grace `arm_fp_*_spec` FLOPs per op  | Intel / AMD 0.9-1.1; Grace at most 4 / 8 (128-bit SVE FMA) |
| `instructions`               | the dp GEMM window                                                      | `instr_retired` (else `instr_retired_any`) vs `perf stat -a -e instructions`                                                                                                                                                               | 0.95-1.05                                                  |
| `energy_pkg` / `energy_dram` | the dp GEMM window                                                      | `pkg_energy` / `dram_energy` (mJ, mod 2^32) vs powercap `energy_uj` of `package-N` / `dram` zones (mod `max_energy_range_uj` + 1); x86 only, AMD has no DRAM domain                                                                        | 0.95-1.05                                                  |

A missing source (no PMU or a sentinel on a counter key, no `perf`, no powercap, a load kernel the installed `canary_gemm_load.sh` lacks, no `taskset`) is `NOT_RUN` with the reason, never `PASS`; a `fast` payload that stops advancing is `FAIL`.
Knobs: `COUNTER_CHECK_SECONDS` (load length, default 20) and `COUNTER_CHECK_TIMEOUT` (default 3600 s); the standalone script also takes `--seconds` and `--results FILE`.
`COUNTER_CHECK=0` skips the layer, and a release RPM (no DEBUG mirror) prints SKIP.
Tests: `tests/test_rpm_debug_counter_check.sh`, `tests/test_counter_accuracy_lib.py`; they stub the PMU, the loads and powercap, so only a real node proves the counters.

## Run the canary checks

The campaign runs these scripts itself; run them alone to debug one counter family.
Each runs as root on an idle node, bounds every wait and command, and stops its load on any exit.

### Generate a known load

`scripts/canary_gemm_load.sh` builds a small self-contained OpenMP GEMM (no BLAS or module) in double, single and half precision under `--prefix` (default `$HOME/hpcperfstats-gemm-load`, or `$HPCPERFSTATS_GEMM_PREFIX`) and runs it in the background with a pidfile.
Half precision needs `_Float16`: aarch64 GCC 11 has it, x86 GCC 11 does not, so `hp` is skipped there.

```bash
./scripts/canary_gemm_load.sh install
./scripts/canary_gemm_load.sh start --precision dp
./scripts/canary_gemm_load.sh status
./scripts/canary_gemm_load.sh stop
./scripts/canary_gemm_load.sh run --precision sp --seconds 600
```

`start` loads all online CPUs until `stop`; `status` prints the pid, settings and last GFLOP/s line; `run` stays in the foreground (for an sbatch script).
Load started outside Slurm is measured by the node-level checks but not attributed to a job; run it with `run` inside a Slurm job to see the CPU Multiprecision Mix pie.
Regression: `tests/test_canary_gemm_load.sh` (`check-local`, skips without OpenMP).

### Check Grace core and SCF counters

`scripts/canary_counter_check.sh` needs the debug RPM installed.
For each of `dp sp hp idle` it starts the GEMM load and measures the monitor's `host_cpu_hw` rates from `fast` against `perf stat -a` over the same window (default 120 s after 60 s warm-up, about 5 min per precision):

- O3: monitor `instr_retired` within 5% of perf `instructions`.
- slice: under each load only that precision's `arm_fp_*_spec` counts.
- accuracy: GEMM GFLOP/s divided by the monitor FP rate must not exceed the 128-bit SVE FMA ceiling (4 / 8 / 16 FLOPs per dp / sp / hp operation), with a 1% margin, or 5% when the core events ran under 50% of enabled time.
  A count scaled up from a fifth of the time scatters by a few percent run to run: dp has read 1-4% low at 20% running while sp and hp passed.
  Monitor and perf both scale by enabled / running time, so only this check catches a count that reads low while another program (for example `nvidia-powerd`) holds most counters.

```bash
./scripts/canary_counter_check.sh
./scripts/canary_counter_check.sh --precisions dp \
  --window 60 --warm 30
./scripts/canary_counter_check.sh --scf
./scripts/canary_counter_check.sh --sharing-only
```

The default run exits 0 only on `RESULT: PASS`.
`--scf` checks the `arm_aarch64_imc` SCF reader: a DRAM-bound dp GEMM (`--size 4096`, matrices larger than L3) compares `dram_cas_reads` / `dram_cas_writes` (64-byte transfers, summed over every `scf<N>` row) with `perf stat -a` on every `nvidia_scf_pmu_<N>` (`cmem_rd_data` 32-byte beats, `cmem_wr_total_bytes`).
Perf starts right after one daemon sample, runs until the first sample after `--window`, and is divided by its own wall time.
PASS needs read and write within 5% and at least 1 GB/s of reads.
`--sharing-only` reads the PMU sharing keys over one `--window` with no load and no perf.
Ctrl-C exits 130; the log `tee` ignores INT and TERM so the "interrupted" line is kept.
Output also goes to `$HOME/hpcperfstats-counter-check/<host>-<epoch>.log`.
Regression: `tests/test_canary_counter_check.sh` (fake shm writer, perf and load; `check-local`).

### Check x86 FLOP events

Every x86 event row in `x86_core_perf.c` is a name from the node's perf event list or a raw `cpu/event=0xNN,umask=0xMM/` spec from vendor docs, verified on that CPU class before it ships.
`scripts/perf_raw_flops_check.sh` checks them with perf only (nothing touches an MSR): per precision it runs a bounded GEMM under `perf stat` and compares the sum of weight x count with the GEMM's exact `done total_flops=` line.

- ratio: FLOPs counted by that precision's events (plus `all` events) divided by GEMM FLOPs, within `--tolerance` (default 10%) of 1.00; an undercount on AMD can mean a missing MERGE pairing.
- leak: FLOPs counted by the other precision's events, below 2% of GEMM FLOPs.

Default events: on Intel the eight `fp_arith_inst_retired.*` widths (weights 1/2/4/8 dp, 1/4/8/16 sp; FMA counts twice); on AMD `fp_ret_sse_avx_ops.all` (total FLOPs only).
Events perf cannot open are reported and skipped; a running share below 100% means multiplexing, so the result is scaled.

```bash
./scripts/perf_raw_flops_check.sh --list
./scripts/perf_raw_flops_check.sh
./scripts/perf_raw_flops_check.sh --precisions dp \
  --event 'dp-512:8:cpu/event=0xc7,umask=0x40/'
```

`--list` prints the CPU, perf version and FP events with encodings; the default run does dp then sp, 20 s each, and exits 0 only on `RESULT: PASS`.
`--event CLASS-LABEL:WEIGHT:EVENT` (repeatable) replaces the defaults: CLASS is `dp`, `sp` or `all`, WEIGHT is FLOPs per count.
Logs go to `$HOME/hpcperfstats-gemm-load/perf_raw_flops/`.
Regression: `tests/test_perf_raw_flops_check.sh` (fake perf, load and cpuinfo; `check-local`).

### Verify Grace DRAM CAS against the SCF PMUs

`./measurement/bin/verify_grace_scf_imc.sh` (Grace node, root, OpenMP compiler) stops `hpcperfstats.service` for the run and restores it on exit.
Its loads are `scripts/canary_gemm_load.sh run --kernel read` (load phase) and `--kernel write` (store phase), each 8 GiB, 8 threads, 20 s, built into `<out>/load` (or `$HPCPERFSTATS_GEMM_PREFIX`) and pinned with `taskset` to NUMA node 0.
Their `read_load done total_bytes_read=` and `write_load done total_bytes_written=` lines are the exact bytes of the timed passes (first touch is in setup).
Write-allocate fills add DRAM reads equal to the bytes written, so the store phase compares only write counters.

It checks:

- A: the SCF units through kernel `perf stat`, without the daemon: `nvidia_scf_pmu_0/cmem_rd_data/` over the read load (`units_load`, 32-byte beats) and `nvidia_scf_pmu_0/cmem_wr_total_bytes/` over the write load (`units_store`, bytes), idle subtracted (`NOT_RUN` when `perf` or the events are missing).
- B: a DEBUG daemon run as a transient unit (broker unreachable, dumps to the result dir, `/dev/shm/hpcperfstatsd-scf-verify` mirror): the `scf0` `dram_cas_reads` delta over the read load and `dram_cas_writes` delta over the write load (`daemon_store_dram_cas_writes`) match the bytes in 64-byte CAS within 0.75-1.5x while `scf1` stays below 0.25x.
- B2: with DCGM refreshing at `sample_freq`, `host_cpu_hw` user util on 8 pinned busy CPUs matches `host_cpu` `/proc/stat` user time within 5% and is not a sample behind mid-load.
- C: the DCGM NVSwitch module is denied and not mapped.

Output: `measurement/results/grace_scf_<host>_<UTC>/summary.txt` (daemon output in `daemon_output.log`, not the rate-limited journal), last line `GRACE_SCF_VERIFY_OK` or `GRACE_SCF_VERIFY_FAIL`.
Parsing and verdicts: `lib/grace_scf_verify.py` (tested in `tests/test_grace_scf_verify.py`).

### Calibrate GPU tensor peaks

`src/nvidia_gpu_estimate.c` has 0 in the HMMA and DMMA columns of CC 10.0 (GB200) and 12.0 (RTX PRO 6000), because NVIDIA publishes per-GPU tensor peaks but no per-SM rate or clock.
`measurement/bin/gpu_tensor_calibrate.sh` measures them on one GPU against the DEBUG monitor's own `nvidia_gpu` row.
It is test-only: the RPM, the spec and `rpm_debug_shm_verify.sh` never build it, and it is never linked into `hpcperfstatsd`.

To calibrate, on the GPU node with the DEBUG monitor running and the GPU otherwise idle:

1. As the user, from `HPCPerfStats/monitor/`, run `measurement/bin/gpu_tensor_calibrate.sh build`.
   It picks the newest `cuda` module >= 12.8 from `module -t spider` (`HPCPERFSTATS_CUDA_MODULES="gcc/15.3.0 cuda/13.3"` overrides; Horizon needs those), finds `nvcc` and cuBLAS (`<nvhpc>/math_libs/<TACC_CUDA_VER>/lib64`, then the CUDA tree `lib64`), and builds `bin/gpu_tensor_calibrate.cu` into `measurement/results/gpu_tensor_calibrate/` (`--prefix` to change; `build-info.txt` records modules, paths, link mode, sha256).
   It links cudart and cuBLAS statically when `libcublas_static.a`, `libcublasLt_static.a`, `libculibos.a` and `libcudart_static.a` exist, so root needs no modules; otherwise it links shared cuBLAS with an rpath and prints a `NOTE`.
   A missing module, `nvcc`, cuBLAS or `cublas_v2.h` is `NOT_RUN` (exit 3) with every path searched.
2. As root, run the exact line step 1 prints, of the form `/<checkout>/monitor/measurement/bin/gpu_tensor_calibrate.sh run`.

`run` reads `/dev/shm/hpcperfstatsd-debug` (`--shm`) for GPU `--device 0` (CUDA ordinals in PCI order, the `nvidia_gpu` row index).
It runs FP16-in / FP32-accumulate `cublasGemmEx` (HMMA) at n = 8192 and 16384 and DGEMM (DMMA) at 4096 and 8192, each for 4 sample intervals (at least `--seconds`, default 20) with 3 idle intervals between (about 31 intervals).
Each load prints its exact FLOPs (calls x 2n^3, warmup excluded) and cudaEvent-timed seconds.
`lib/gpu_calibrate_eval.py` averages `tensor_hmma_active` / `tensor_dmma_active` (%) and `gpu_sm_clock` (MHz) over samples wholly inside each load except the first, requires steady activity (5 points) and an idle pipe before and after, and computes FLOP/s / (activity x SMs x clock); the two sizes must agree within 5%.
A DGEMM that leaves `tensor_dmma_active` idle (no FP64 tensor pipe) gives `NOT_PRESENT` and a 0 column.
Output: `measurement/results/gpu_tensor_<host>_<UTC>/` (`summary.txt`, `load_*.txt`, `samples/`), ending with `ROW {major, minor, lanes, {...}},` (the `g_peaks` row to paste) and `GPU_TENSOR_CALIBRATE_OK` / `_FAIL` / `_NOT_RUN`.
On an H100 or GH200 node (sm_90) the run should reproduce the CC 9.0 row's published 4096 / 256, which checks the method.
Tests: `tests/test_gpu_toolchain.sh`, `tests/test_gpu_tensor_calibrate.sh`, `tests/test_gpu_calibrate_eval.py`.

## Inject faults

Live fault injection (`FAULT_INJECT`, default 1; `FAULT_INJECT=0` skips; release RPMs print a SKIP line) runs last.
`scripts/fault_inject_verify.sh` restarts the DEBUG daemon with `debug_fault_inject` for `FAULT_INJECT_TICKS` (3) cycles after one clean cycle on every injectable point the node has, captures the samples the plan needs, restores the conf, and checks:

- the mapped codes land on only the planned keys;
- values come back after release, where the read path does not latch;
- bridgeable gaps keep counters monotonic;
- every site in `sentinel_sites` fired: a never-fired site is a NOTE when `make check` forces its file (`scripts/lib/fault_ci_files.py`) or its collector is not active on the node, else FAIL.

The `debug_fault_inject` conf key forces failures as `point:subject-substring!errno@[delay+]cycles` (`@1+3`: one clean cycle so the rows exist, then three failing ones).
Fault points: `open`, `opendir` (I/O primitives), `perf_open` (event name), `mad_query` (HCA), `dcgm_fetch`, `nvml_fetch` (the NVML+GPM sample; the next collect uses DCGM), `hwmon_read` (meter name), `scandir` (`host_proc`), `statfs` (`host_tmpfs`) and `net_rebuild_slow` (`host_net`).

Plan rules:

- On x86 the plan adds `perf_open:instructions!16` (`EBUSY`).
- `host_ib` points run only when a non-OPA port's `link_layer` is InfiniBand or absent, as the daemon collects, so RoCE-only nodes skip them; `perf_open` is planned from begin.
- The `/sys/class/infiniband` opendir fault runs last and alone: it stops the port walk and its directory latch lasts until the restart.
- With the slow tier a cycle writes either `fast` or `full`, so the capture reads both mirrors, numbers samples by header timestamp, and counts a code when either mirror shows it inside the entry's window; the resume check reads the first `fast` and the first `full` sample after release.
- With `nvidia_gpu` in the schema both GPU points are planned: `nvml_fetch` first, then `dcgm_fetch` after the `nvml_fetch` resume check, so on an NVML+GPM node it fires on the DCGM fallback.
- A fired `nvml_fetch` passes when, after release, every GPU row is still emitted and not frozen at the pre-fault reading, or carries `BACKEND_ERROR` on every key (DCGM unusable); vanished or frozen rows FAIL.
  The DCGM path's `NOT_PRESENT` / `READ_FAILED` / `INVALID_VALUE` keys (GPM-only `tensor_dmma_active`, fields its watch profile lacks) are not out of plan.
- A planned point that `fault_fired` reports with 0 fires (`nvml_fetch` on a DCGM-served node, `dcgm_fetch` when DCGM never fetches) is a NOTE; a daemon without `fault_fired` keeps the FAIL.
- When no planned point fired the layer is `NOT_RUN` (`fault_inject: NOT_RUN` line and `fault_inject` check in the JSON).

DEBUG builds register every `stats_set_missing*` call site in a linker-section registry and write these mirror files:

- `/dev/shm/hpcperfstatsd-debug/sentinels`: each non-OK key as `type dev key code slug errno site first_ts last_ts count`.
- `sentinel_sites`: every site with its hit count.
- `fault_fired`: each configured `debug_fault_inject` entry as `<entry> <fires>` (empty without one).

Each state transition is logged once.
In `make check`, `tests/test_fault_inject_matrix.c`, `tests/test_fault_inject_hw.c`, `_fs.c`, `_cpu.c` and the coverage twins of the collector sentinel tests (all built with `-DDEBUG`) FAIL on any linked failure site never exercised; together they link every src file with a site (`scripts/lib/fault_ci_files.py`, checked by `tests/test_fault_ci_files.sh`).
`scripts/check_sentinel_error_paths.py src` (in `make check`) flags `stats_set(..., 0)` or a silent `continue` / `return` / `goto` after a failed read; intentional cases go in `scripts/check_sentinel_error_paths.allowlist` with a reason.

## Verify the result

### Read the campaign summary JSON

Every run, an early failure included, writes one file per node next to the text reports: `test_runs/monitor/correctness_<host>_<slug>_<UTC>.json` (`scripts/lib/correctness_summary.py`; schema locked by `tests/test_correctness_summary.py`).
Schema version 1 has these fields:

- `schema_version`: `1`; bumped only on a breaking change.
- `generated_utc`: ISO-8601 UTC, seconds.
- `host`: node hostname.
- `arch`: `class` (`intel`, `amd`, `grace`, `other`), `vendor`, `family`, `model` (x86 cpuinfo; Grace implementer / part), `pmu_name` (`cpu/caps/pmu_name`, else `none`).
- `capability_slug`: slug of the installed build (`unknown` before capabilities resolve).
- `rpm_build`: `rpm -q hpcperfstatsd`.
- `debug_build`: `configure.debug` of the capabilities JSON.
- `overall`: `FAIL` when any check FAILs, else `PASS`.
- `counts`: number of checks per status.
- `checks`: every check, always all of them in inventory order, each `{name, layer, status, reason, ratio}` with status `PASS` / `FAIL` / `NOT_RUN` / `SKIP` and `ratio` the daemon / reference ratio (Grace FLOP checks: FLOPs per op) or null.

`NOT_RUN` means the check could not measure (missing source, or not reached after an earlier failure); `SKIP` means it was switched off or does not apply to the build.

### Correctness check inventory

Every correctness check in `HPCPerfStats/monitor`, and whether `rpm_debug_shm_verify.sh` runs it: a check is wired when it runs unattended as root on a compute node in bounded time.
Each entry gives what the check proves, whether it is wired, and its opt-out switch with the summary name (the `checks[].name` value, in JSON order).

- install / service / first full payload: DEBUG RPM installed, `hpcperfstats.service` active, full (and with the slow tier, fast) payloads with rows, slug resolved; the same bounded wait follows every daemon restart.
  Wired: Yes; switch (summary name): always (`preflight`).
- `validate_shm_messages.py` structure: schema, fast and full rows parse against the capability manifest; listend host contract.
  Wired: Yes; switch (summary name): always (`message_structure`).
- value sentinel layers: `$sentinels` banner, allowed codes per key, `NOT_PRESENT` checked against the live source, no key `NOT_UPDATED` twice; BeeGFS RDMA / SDP mounts read `NOT_PRESENT`.
  Wired: Yes; switch (summary name): `STRICT_SENTINELS`, `SENTINEL_PROBE_CHECK` (`sentinels`).
- plausibility: values inside plausible ranges.
  Wired: Yes; switch (summary name): `STRICT_PLAUSIBILITY` (`plausibility`).
- cross-sample: cadence, width-aware `E`-counter monotonicity, bridge validity; the stimulus touches a Lustre mount so the llite source (procfs, else debugfs on Lustre 2.15) must move.
  Wired: Yes; switch (summary name): `CROSS_SAMPLE_CHECK`, `STRICT_CROSS_SAMPLE` (`cross_sample`).
- live spot: emitted values match live `/proc`, `/sys`, `nvidia-smi` reads; `host_power_socket` uses the next fresh `fast` (else `full`) sample, NOTE when none lands within `--spot-check-max-age`.
  Wired: Yes; switch (summary name): `STRICT_LIVE_SPOT_CHECK` (`live_spot`).
- independent read: a live read of the collector's source lies between two emitted values; every `host_ib` counter is sysfs `counters/` (`sw_*`, the only MAD keys, have no host file).
  Wired: Yes; switch (summary name): `INDEPENDENT_READ_CHECK` (`independent_read`).
- consumer parse: sentinels never enter a rate, no negative delta, Codon parity when built.
  Wired: Yes; switch (summary name): `CONSUMER_PARSE_CHECK` (`consumer_parse`).
- golden diff: byte diff against a slug golden.
  Wired: Opt-in; switch (summary name): `GOLDEN_CHECK=1` / `GOLDEN_DIR` (`golden`).
- counter accuracy: daemon memory bytes, FLOPs, instructions, energy vs known loads.
  Wired: Yes; switch (summary name): `COUNTER_CHECK` (`mem_read`, `mem_write`, `mem_socket0`, `flops_dp`, `flops_sp`, `instructions`, `energy_pkg`, `energy_dram`).
- `perf_raw_flops_check.sh`: x86 perf FLOP encodings vs exact GEMM FLOPs, no daemon.
  Wired: Intel / AMD; NOT_RUN on Grace; switch (summary name): `PERF_RAW_FLOPS_CHECK` (`perf_raw_flops`).
- `canary_counter_check.sh --scf`: Grace SCF `dram_cas_*` vs perf on every `nvidia_scf_pmu_<N>` within 5%.
  Wired: Grace; NOT_RUN without SCF PMUs; switch (summary name): `SCF_PERF_CHECK` (`scf_perf`).
- `canary_counter_check.sh --sharing-only`: PMU sharing keys: running share \<= 100% on a quiet node.
  Wired: Yes; switch (summary name): `PMU_SHARING_CHECK` (`pmu_sharing`).
- `canary_counter_check.sh` precision mode: Grace precision slices, `instr_retired` within 5%, FLOPs-per-op ceiling.
  Wired: Grace; NOT_RUN on x86; switch (summary name): `FP_SLICES_CHECK` (`fp_slices`).
- live fault injection: forced failures map to the planned codes, values recover, gaps bridge, every site fires.
  Wired: Yes; release RPMs SKIP; switch (summary name): `FAULT_INJECT` (`fault_inject`).
- `measurement/bin/verify_grace_scf_imc.sh`: Grace SCF with perf and a transient DEBUG daemon, DCGM cadence, NVSwitch denied.
  Wired: No: stops the service and needs a build tree; switch (summary name): None.
- `validate_stampede3_profile.sh`: validate layers plus the Stampede3 type-presence contract.
  Wired: No: needs `--profile` and repeats this run's layers; switch (summary name): None.
- BeeGFS TCP byte countability: `vfs_*_bytes` equal TCP bytes to the storage servers; RDMA gives `NOT_PRESENT`.
  Wired: Partly (sentinel layer); byte-exact needs a mount and controlled I/O (`tests/test_beegfs_tcp.c`, `test_beegfs_io.c`); switch (summary name): None.
- Lustre llite procfs / debugfs source: opcode stats from procfs, else debugfs; neither gives `NOT_PRESENT`.
  Wired: Partly (cross-sample, sentinels); byte-exact needs a mount and known load; switch (summary name): None.
- `test_rabbitmq_integration.sh`: payloads survive a real broker and the consumer contract.
  Wired: No: needs a broker (`RUN_RMQ_INTEGRATION=1`); switch (summary name): None.
- `check_sentinel_error_paths.py`, `check_fault_ci_files.py`, `check_emitted_variable_names.py`, `check_unsafe_c_patterns.sh`, C unit and fault-matrix tests: source lint and unit contracts.
  Wired: No: `make check` build-tree checks; switch (summary name): None.
- `run_valgrind_check.sh`, `run_cpp_linter.sh`, `cross_compile_test.sh`, `run_warning_gate.sh`, `run_shared_node_static_check.sh`: memory safety, format, foreign builds, warnings.
  Wired: No: build-host gates; switch (summary name): None.
- measurement kit (`run_overhead_profile.sh`, `capture_jitter.sh`, `compare_schema_keys.py`, cadence capture): daemon overhead, jitter, schema keys kept between two runs.
  Wired: No: stops the daemon or compares two runs; switch (summary name): None.
- `catalog_hardware_error_packet.py`, peak SQL checks: Job Detail hardware-error coverage, roofline peaks in `host_data`.
  Wired: No: needs the database; switch (summary name): None.
- `track_cpu_peg.sh`: CPU peg triage bundle.
  Wired: No: a 20-minute capture, not pass / fail; switch (summary name): None.

### Aggregate the campaign matrix

On the dev host, `scripts/correctness_matrix.py test_runs/monitor/campaign` aggregates every node's JSON (directories are searched recursively for `correctness_*.json`; `--class-map HOST=milan-cpu` maps an operator-picked node) into one matrix.
Rows are campaign classes, columns the checks, cells `P 1.00` / `F 0.62` / `N` / `S`.
Only files with `schema_version` 1 and `debug_build` true count, and the newest file per host wins.
`scripts/lib/correctness_classes.tsv` maps hosts to classes (unmapped hosts print as `?<host>`); hosts of one class merge with the worst status winning (FAIL > NOT_RUN > SKIP > PASS).
`scripts/lib/correctness_expected.tsv` holds the expected status per class and check (`P`, `N`, `S`, or `P/N`; override with `--expected FILE`), and every PASS ratio of a counter-accuracy check is re-checked against the `counter_accuracy.py` tolerances.
The run exits 1 on any FAIL, unexpected `NOT_RUN` / `SKIP`, ratio outside tolerance, arch mismatch or class without a file, and 2 on unreadable or invalid input; `--json` prints the result for machines.
Tests: `tests/test_correctness_matrix.py`.

## Troubleshooting

- Sentinel layers report `SKIP`: the capture has no `$sentinels` banner because `emit_value_sentinels 0` is set; set it to 1.
- A check is `NOT_RUN`: read its `reason` in the JSON; it names the missing source (PMU, `perf`, powercap, `taskset`, load kernel) or the earlier failure that stopped the run.
- `== KEEP debug RPM: ...` after `campaign_verify_node.sh`: a step returned non-zero; the line names each step rc, and the logs are in `/tmp/hps-cc-<host>/`.
- The hourly release status line adds `sentinels: SLUG=n ...` (tokens emitted per code since the last hour, `sentinels: none` when there were none); a code that keeps rising on a release node points at the collector to check with a DEBUG run.
- To soften a run while debugging, set `CROSS_SAMPLE_CHECK=0 STRICT_PLAUSIBILITY=0 STRICT_LIVE_SPOT_CHECK=0`, or opt out of one sentinel layer with `STRICT_SENTINELS=0`, `SENTINEL_PROBE_CHECK=0`, `INDEPENDENT_READ_CHECK=0` or `CONSUMER_PARSE_CHECK=0`.

## Related

- [RAW_DATA_FORMAT.md](../RAW_DATA_FORMAT.md)
- [Build the RPM](build-the-rpm.md)
- [Profile the daemon](profile-the-daemon.md)
- [About x86 counters and roofline peaks](../explanation/x86-counters-and-roofline.md)
- [Source modules](../reference/source-modules.md)
