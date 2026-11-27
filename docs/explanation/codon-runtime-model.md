# Codon runtime model

This page explains how the baked Codon kernels run inside `sync_timedb` and `update_metrics`, and why they run that way.
It is for developers who change a Codon compile unit or the bridge that loads it.

Codon compiles only the numeric hot loops; the daemons stay on free-threaded `/opt/python3.14t`.
`sync_timedb` diverts each file's feed -> counter deltas -> collapse -> arc path (`feed_delta_collapse_arc`) into Codon worker processes.
`update_metrics` calls single-threaded Codon kernels from its metrics threads.
Django ORM, COPY I/O, the queue orchestrator, archives and Bokeh stay in CPython.
The CPython and NumPy twins are the reference oracle; parity rules are in `codon-python-parity-contract.mdc`.

## sync_timedb parse kernels

`hpcperfstats/dbload/codon/` holds the production compile units:

- `feed_collapse.py`: class-free Codon-NumPy feed -> delta -> columnar collapse -> arc.
- `feed_collapse_capi.py`: the `@export` C ABI (fingerprint, line parse, file session, COPY exports) and the only file compiled.
- `pyext_ft_init.c`: the multi-phase `Py_MOD_GIL_NOT_USED` trampoline with a slot-aware marshal.
- `build_codon_ext.sh`: the in-image bake helper ([Build Codon kernels](../how-to/develop/build-codon-kernels.md)).

`feed_collapse.py` defines no classes, because Codon custom classes lack `__to_py__` at the pyext boundary: the schema is a 4-tuple of lists and the feed state is free functions over module-level constants such as `HOST_PROC_KEYS`.
It ships with `RUN_MAIN = False`, so importing it never runs the bench entry.
`feed_collapse_capi.py` is Codon-only (`@export`, `str.from_ptr`), so CPython unit tests never load it.
The C ABI takes a per-call event-type exclusion list per slot (`codon_exclude_set_c`).

## Process model

File parses run in Codon worker processes (`python -m hpcperfstats.dbload.lib.codon_process_worker`, `codon_mode=process_pool`), one per ingest thread.
`[PIPELINE] sync_ingest_pool_processes` sets the count, capped at `MAX_CODON_SLOTS` (128); no separate key sets the worker count.
Each process has its own Codon runtime and Boehm GC, because threads sharing one runtime serialize on Boehm's allocation lock; processes are the parallel unit.

Each file streams through `[PIPELINE] sync_codon_parse_flush_max_mib` (1 MiB) windows, because the Boehm heap follows output rows.
The heap never shrinks, so a worker above `[PIPELINE] sync_codon_worker_recycle_rss_mib` is replaced between files.
The measured heap sizes and the RSS roof trade-off are in [Pipeline concurrency](pipeline-concurrency.md#codon-worker-heap); the pool table is in [sync_timedb threads and processes](../reference/sync-timedb-threads-and-processes.md#e-codon-worker-processes).

Line-batch parses (`parse_stats_lines_codon`) and host test fakes stay in-process on per-slot locks.
No lock serializes the Codon runtime process-wide (`codonrt_serialize=0`); the concurrent I/O stress test proves it with `slot_occ_max>1` in `test_runs/codon_stripe_stress_*.log`.
The bridge that owns slots, workers and the startup line is `hpcperfstats/dbload/lib/sync_timedb_codon_bridge.py`.

## Failure model

A bridge that never enabled at boot (`status=disabled`, missing `.so`) leaves the CPython `IncrementalStatsParser` as the sole parse path.
Once the bridge is on, Codon finishes delta, collapse and arc (`DeltaCarryState.codon_finished_chunk`), and ingest skips `compute_deltas_and_arc_chunk`.
A Codon, IPC or kernel failure mid-file, including `codon worker EOF`, fails that file; it never falls back to CPython, so a parse bug cannot silently change rows.

## update_metrics kernels

`hpcperfstats/analysis/metrics/codon/metrics_kernels.py` holds the class-free kernels; CPython keeps the ORM, COPY I/O, probe orders, name resolution, sentinel classification and Bokeh.

- `copy_count_rows_c`, `copy_decode_c`: binary COPY decode of `real` or `double precision` value fields into float64, with chunk-local name tables.
- `group_reduce_c`: count, sum and max per int64 key, with stable row-order sums.
- `residual_collapse_c`: shared-node hardware SUM, else `j:` MAX, per key.
- `grid_mean_c`, `join_sorted_c`: grid means and sorted joins.
- `sentinel_samples_c`: per-sample valid, bridged and gap states, with row-order sums.
- `sentinel_fold_c`, `fill_interior_gaps_c`: per-host 5-minute bucket means, gap flags, first-bucket drop and interior fill.

`pyext_metrics_init.c` builds the module `hpcperfstats_codon_metrics`: multi-phase, `Py_MOD_GIL_NOT_USED`, no state, and it borrows NumPy buffers through the buffer protocol.
The bridge is `hpcperfstats/analysis/metrics/lib/columnar/codon_bridge.py`; the NumPy references are `segments.py`, `k0_decode.py` and `hpcperfstats/analysis/metrics/lib/sentinel_profile.py`.

Each metrics-pool thread runs one job, and the kernels are single-threaded and write into caller-allocated NumPy arrays.
Threads therefore never contend on Boehm's allocation lock; the decoder's distinct-name dictionary is the only heap use.
A thread registers with the bundled `libcodonrt` collector on its outermost kernel call.
`scripts/metrics_codon_scaling_report.py` gates thread scaling at 0.6x linear or better at 8 threads.

On the pipeline interpreter the kernel is required: without it the scheduler refuses to start, and with it a kernel failure fails the job, with no NumPy fallback.
Host `.venv` runs use the NumPy reference.
The full contract is `update-metrics-columnar-codon-contract.mdc`.

## Alternatives and trade-offs

- Threads sharing one Codon runtime would avoid IPC, but Boehm's allocation lock makes per-file parse time grow with thread count; worker processes scale with the ingest width at the cost of RSS per worker.
- Metrics kernels stay in-process because they allocate nothing per row, so the allocation lock never binds.
- Baking in the image with `--mcpu=native` ties the kernels to the build host's CPU, which is why the image is built on the host that runs it.
- Falling back to CPython after a mid-file failure would hide kernel bugs as missing or different rows, so failure is fatal for that file or job instead.

## Related

- [Build Codon kernels](../how-to/develop/build-codon-kernels.md)
- [Pipeline concurrency](pipeline-concurrency.md)
- [sync_timedb threads and processes](../reference/sync-timedb-threads-and-processes.md)
- [update_metrics state machine](../reference/update-metrics-state-machine.md)
