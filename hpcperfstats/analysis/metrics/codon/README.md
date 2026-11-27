# Codon update_metrics kernels

This directory holds the Codon compile unit of the columnar `update_metrics` path: `metrics_kernels.py`, `pyext_metrics_init.c` and the bake helper `build_codon_metrics_ext.sh`.
The Dockerfile stage `codon-build` bakes them into `/opt/hpcperfstats/codon_metrics`; on the pipeline interpreter the scheduler refuses to start without the baked kernel.
Rebuild with:

```bash
./scripts/rebuild_pipeline.sh
```

- [Build Codon kernels](../../../../docs/how-to/develop/build-codon-kernels.md): bake, smoke gates, verify.
- [Codon runtime model](../../../../docs/explanation/codon-runtime-model.md#update_metrics-kernels): what each kernel does and how threads call them.
- Contract: `hpcperfstats/cursor-rules/update-metrics-columnar-codon-contract.mdc`.
