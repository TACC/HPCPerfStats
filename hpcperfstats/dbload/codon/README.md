# Codon parse+collapse (production bake sources)

Production Codon-NumPy compile units for `sync_timedb` feed+collapse.

## In-Dockerfile bake (only supported path)

1. Stage **`codon-build`** in `services-conf/pipeline_web.Dockerfile` (FROM
   `python-build`) downloads pinned Codon, runs `build_codon_ext.sh`, and
   fails closed unless `codon_pyext_314t=pass`.
2. Runtime stage **`COPY --from=codon-build`** into
   `/opt/hpcperfstats/codon_parse` (pyext + kernel + Codon runtime libs with
   `$ORIGIN` rpath). No `/opt/codon` in the slim image.
3. `services-conf/supervisord.conf` sets
   `PYTHONPATH=/opt/hpcperfstats/codon_parse` for `sync_timedb`.
4. On daemon start, grep logs for
   `codon_parse status=enabled` (or `disabled` if fail-closed).

Rebuild via `./scripts/rebuild_pipeline.sh` (or full-site rebuild). Do **not**
host-prebuild `.so` files, stage a `codon_parse_image/` tree into the build
context, or run `build_codon_ext.sh` on the host as the prod artifact path.

Benches/inventory stay under `experiments/codon/`.

| Path | Role |
|------|------|
| `feed_collapse.py` | Class-free Codon-NumPy feed → delta → columnar collapse → arc |
| `feed_collapse_capi.py` | `@export` C ABI (fingerprint, line parse, file session, COPY exports) |
| `pyext_ft_init.c` | Multi-phase `Py_MOD_GIL_NOT_USED` FT trampoline (slot-aware marshal) |
| `build_codon_ext.sh` | In-image bake helper (Dockerfile `codon-build` only) |
| `VERSION` | Pinned Codon release (must match Dockerfile `CODON_VERSION`) |

Contract: `hpcperfstats/cursor-rules/codon-sync-timedb-parse-contract.mdc`.
Operator note: `docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md` (Codon parse bridge).
