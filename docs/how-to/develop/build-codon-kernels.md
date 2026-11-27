# Build Codon kernels

This page shows developers and operators how to bake the Codon kernels into the pipeline image, check that they load, and run the Codon experiments.
Use it when you change a Codon compile unit, its pyext C file or build script, or the pinned Codon release.

> [!IMPORTANT]
> The Dockerfile stage `codon-build` is the only supported build path.
> Never ship a host-built `.so`, stage a `codon_parse_image/` tree into the build context, or treat a host run of `build_codon_ext.sh` as the production artifact.

## Prerequisites

- You build on the pipeline host that runs the image: the kernels compile for the native CPU, and a weaker build host understates the production speedup.
- The Codon release is the one pinned by `CODON_VERSION` and `CODON_TARBALL_SHA256` in `services-conf/pipeline_web.Dockerfile`.

## Bake the kernels into the image

To bake both kernel sets:

1. Rebuild the pipeline image (or the full site):

   ```bash
   ./scripts/rebuild_pipeline.sh
   ```

2. Read the `codon-build` stage output in the build log.
   The stage, `FROM python-build`, downloads and checks the pinned Codon into `/opt/codon`, runs one build script per product, and fails closed unless its smoke tokens print.

The runtime stage copies only the output directories with `COPY --from=codon-build`; `/opt/codon` is not in the slim image.
Each directory holds the pyext, its kernel library (`libhpcperfstats_codon_kernel.so` or `libhpcperfstats_codon_metrics_kernel.so`) and the Codon runtime libraries (`libcodonrt.so`, `libomp.so`, `libgfortran.so.5`, `libquadmath.so.0`, `libgcc_s.so.1`).
The pyext links the kernel with `-Wl,-rpath,$ORIGIN`, so everything loads from that one directory.

| Product          | Build script                                                     | Output and `PYTHONPATH` for the daemon                              | Hard smoke tokens                                        |
| ---------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------- | -------------------------------------------------------- |
| `sync_timedb`    | `hpcperfstats/dbload/codon/build_codon_ext.sh`                   | `/opt/hpcperfstats/codon_parse` (`hpcperfstats_codon_parse.so`)     | `codon_pyext_314t_gil_off=pass`, `codon_pyext_314t=pass` |
| `update_metrics` | `hpcperfstats/analysis/metrics/codon/build_codon_metrics_ext.sh` | `/opt/hpcperfstats/codon_metrics` (`hpcperfstats_codon_metrics.so`) | `codon_metrics_pyext_314t=pass`                          |

`services-conf/supervisord.conf` sets each `PYTHONPATH`, and both bridges also add their directory to `sys.path` when it exists.
Do not put a `hpcperfstats_codon_parse.py` stub on `PYTHONPATH`: it shadows the extension.

Both scripts build the same way:

- The kernel compiles with `codon build -release --mcpu=native --linker-flags "-flto=thin" --lib` (never `--disable-native`); the parse kernel exports `feed_and_collapse_fingerprint_c`, `parse_stats_lines_codon_run_c` and the file-session entries.
- The pyext C file compiles with `-march=native -mtune=native -flto=thin` against the `/opt/python3.14t` headers, as a multi-phase module that declares `Py_MOD_GIL_NOT_USED`.
- Codon calls `g++` by name, so the script puts a `g++` -> `clang++` shim first on `PATH`.
- The bake log prints `target-cpu` and `tune-cpu` and a `simd_insn_count zmm=... ymm=... xmm=...` total.

The parse smoke runs under free-threaded `/opt/python3.14t` with no `/opt/codon` on `LD_LIBRARY_PATH`.
It checks that `sys._is_gil_enabled() is False` after import, an empty and an E6 fingerprint, and a `parse_stats_lines_codon` finished dict with `delta` rows and no `wid` or `mult`.
The GIL `/opt/python3.14` import of the same `.so` is best effort (`codon_pyext_gil=pass` or `skip_ft_abi`).
The metrics smoke imports with the GIL off, runs every kernel and checks that a name-table overflow is reported (`codon_metrics_name_cap_overflow=pass`).

To test a bake before it is in the image, see [Test an unbaked Codon metrics build](diagnose-update-metrics.md#test-an-unbaked-codon-metrics-build) and the `HPCPERFSTATS_CODON_PYEXT_DIR` parity run in [Test layout](../../reference/test-layout.md#sync_timedb).

## Run the Codon experiments

`tests/experiments/codon/` holds the A/B harness, the compile inventory and microbenches; none of it is a production artifact.

To run them:

1. Optional: install the pinned Codon on the host for the inventory and `codon run` spikes:

   ```bash
   F=services-conf/pipeline_web.Dockerfile
   V=$(sed -n 's/.*CODON_VERSION=\([0-9.]*\).*/\1/p' "$F")
   D=/data/user/$USER/codon-$V
   U=https://github.com/exaloop/codon/releases/download
   mkdir -p "$D"
   curl -fsSL -o /tmp/codon-$V.tar.gz \
     "$U/v$V/codon-linux-x86_64.tar.gz"
   tar -xzf /tmp/codon-$V.tar.gz -C "$D" --strip-components=1
   export CODON_HOME=$D
   ```

2. Run the micro A/B; Arm A is the 3.14t `IncrementalStatsParser` plus `compute_deltas_and_arc`, and Arm B is the image-baked pyext:

   ```bash
   ./tests/experiments/codon/run_codon_ab_baseline.sh
   ```

   It passes when `test_runs/codon_ab_*.log` shows `gate_1p25x=True` (Arm A over Arm B at 1.25x or more).

3. After a change that widens the Codon surface, run `run_sync_timedb_compile_inventory.sh`; it attempts a Codon build of every `sync_timedb*.py` and writes `test_runs/codon_sync_timedb_inventory_*.log`.

A full-load win needs the `--loaded48` soak with Codon off and on ([Run the loaded soak](run-sync-timedb-benchmarks.md#run-the-loaded-soak)): `mean_files_per_s` B/A of 2.0 or more, with `codon_slot_occ` held above 0 on Arm B.
`parse_kernel.py` and `e6_corpus.py` are the CPython oracle and the corpora.

The host tests for the parse kernel and bake contract:

```bash
/data/HPCPerfStats/.venv/bin/python3 -m pytest \
  hpcperfstats/tests/test_codon_feed_collapse_class_free.py \
  hpcperfstats/tests/test_codon_feed_gpu_j_parity.py \
  hpcperfstats/tests/test_codon_runtime_bake_contract.py \
  hpcperfstats/tests/test_sync_timedb_codon_bridge.py -q
```

## Verify the result

To verify the parse bridge after a redeploy:

1. Grep the startup line:

   ```bash
   podman-compose -p hpcperfstats logs pipeline 2>&1 \
     | grep 'codon_parse status=' | tail -5
   ```

2. Confirm `codon_parse status=enabled ... api=parse_stats_lines_codon gil=off contexts=<N> codon_mode=process_pool codonrt_serialize=0`.
   `<N>`: the slot count.

3. Confirm the daemon interpreter is still `/opt/python3.14t/bin/python3.14t` ([Run T0](../operate/verify-sync-timedb.md#run-t0)) and that `sys._is_gil_enabled()` stays false after the Codon import.

4. Grep the census lines for `codon_slot_occ=` (held over total slots).

5. Grep for `codon_metrics status=enabled` from `update_metrics`.

To check a rebuilt image without starting the stack:

```bash
podman run --rm --entrypoint /bin/bash \
  localhost/hpcperfstats:latest -lc '
  export PYTHONPATH=/opt/hpcperfstats/codon_parse
  M=hpcperfstats.dbload.lib.sync_timedb_codon_bridge
  /opt/python3.14t/bin/python -c "
import importlib
b = importlib.import_module(\"$M\")
print(b.log_codon_parse_startup())"
'
```

## Troubleshooting

### codon_parse status=disabled

The `reason=` names the cause: `extension_unavailable` (no `.so` on the path), `gil_enabled_after_import`, or `HPCPERFSTATS_CODON_PARSE_DISABLE`.
`HPCPERFSTATS_CODON_PARSE_DISABLE=1` turns the bridge off in tests and soaks and logs `status=disabled reason=HPCPERFSTATS_CODON_PARSE_DISABLE`.
With the bridge off, ingest runs the CPython parser and the stall signatures are the same.

### codon_metrics status=disabled and update_metrics exits

On the pipeline interpreter a missing `hpcperfstats_codon_metrics.so` logs `codon_metrics status=disabled ... required=1` and the scheduler refuses to start; production has no NumPy fallback.
Rebuild the image.

### Files fail with Codon errors

Escalate a sustained flood of `codon parse_stats_lines_codon failed`, `gil_enabled_after_import` or mid-file `RuntimeError` lines, including `codon worker EOF`.
A Codon failure fails that file and never falls back to the CPython parser ([Failure model](../../explanation/codon-runtime-model.md#failure-model)).
Codon is not a fix for day_close, lock or discover stalls.

## Related

- [Codon runtime model](../../explanation/codon-runtime-model.md)
- [Verify sync_timedb after a deploy](../operate/verify-sync-timedb.md)
- [Run sync_timedb benchmarks](run-sync-timedb-benchmarks.md)
- [Diagnose update_metrics](diagnose-update-metrics.md)
