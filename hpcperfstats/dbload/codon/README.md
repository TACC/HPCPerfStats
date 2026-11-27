# Codon parse kernels

This directory holds the Codon compile units that `sync_timedb` uses to parse and collapse stats files: `feed_collapse.py`, `feed_collapse_capi.py`, `pyext_ft_init.c` and the bake helper `build_codon_ext.sh`.
The Dockerfile stage `codon-build` bakes them into `/opt/hpcperfstats/codon_parse`; rebuild with:

```bash
./scripts/rebuild_pipeline.sh
```

- [Build Codon kernels](../../../docs/how-to/develop/build-codon-kernels.md): bake, smoke gates, verify.
- [Codon runtime model](../../../docs/explanation/codon-runtime-model.md#sync_timedb-parse-kernels): what each file does and how the workers run.
- Contract: `hpcperfstats/cursor-rules/codon-sync-timedb-parse-contract.mdc`.
