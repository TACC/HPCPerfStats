# Codon update_metrics kernels (production bake sources)

Codon compile unit for the columnar update_metrics path (plan
`update-metrics-codon-columnar`, Stage 6). CPython keeps the ORM, COPY I/O,
probe orders, name resolution and Bokeh; this unit holds the two hot loops:

| Path | Role |
|------|------|
| `metrics_kernels.py` | Class-free Codon kernels over caller buffers: `copy_count_rows_c` / `copy_decode_c` (K0 binary COPY decode with chunk-local name tables), `group_reduce_c` (count/sum/max per int64 key, stable row-order sums), `grid_mean_c`, `join_sorted_c` |
| `pyext_metrics_init.c` | Multi-phase `Py_MOD_GIL_NOT_USED` module `hpcperfstats_codon_metrics`; borrows NumPy buffers (buffer protocol), no state |
| `build_codon_metrics_ext.sh` | In-image bake helper (Dockerfile `codon-build` stage only) |

Bridge and NumPy reference: `hpcperfstats/analysis/metrics/lib/columnar/codon_bridge.py`,
`segments.py`, `k0_decode.py`. Contract rule:
`hpcperfstats/cursor-rules/update-metrics-columnar-codon-contract.mdc`.

## In-Dockerfile bake (only supported path)

1. Stage **`codon-build`** in `services-conf/pipeline_web.Dockerfile` runs
   `build_codon_metrics_ext.sh` after the dbload parse bake and fails closed
   unless `codon_metrics_pyext_314t=pass` (import under `/opt/python3.14t`
   with the GIL off, kernel smoke, name-table overflow reported).
2. Runtime stage **`COPY --from=codon-build`** into
   `/opt/hpcperfstats/codon_metrics` (pyext + kernel + Codon runtime libs with
   `$ORIGIN` rpath).
3. `services-conf/supervisord.conf` sets
   `PYTHONPATH=/opt/hpcperfstats/codon_metrics` for `update_metrics`; the
   bridge also self-wires the directory onto `sys.path`.
4. On daemon start grep for `codon_metrics status=enabled`. On the pipeline
   interpreter a missing `.so` logs `codon_metrics status=disabled` and the
   scheduler **refuses to start** (no NumPy fallback in production).

Rebuild via `./scripts/rebuild_pipeline.sh` (or full-site rebuild). Do **not**
ship a host-built `.so` as the prod artifact. For fast iteration on the dev
host, run the script in a `codon-build` container with the host Codon mounted
at `/opt/codon` and point `HPCPERFSTATS_CODON_METRICS_BUILD_DIR` at the output
when running `tests/run_dev_container_pytest.sh --ft`.

## Runtime model

One job per metrics-pool thread; kernels are single-threaded and write into
caller-allocated NumPy arrays, so threads never contend on Boehm's
allocation lock (the decoder's distinct-name dictionary is the only heap
use). Threads register with the bundled `libcodonrt` collector on their
outermost kernel call. `scripts/metrics_codon_scaling_report.py` measures
thread scaling (gate: >= 0.6x linear at 8 threads).
