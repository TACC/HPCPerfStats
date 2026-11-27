# Codon experiments

This directory holds the Codon A/B harness, the `sync_timedb` compile inventory and microbenches; the production compile units are in `hpcperfstats/dbload/codon/`.
Run the micro A/B against the image-baked pyext with:

```bash
./tests/experiments/codon/run_codon_ab_baseline.sh
```

[Build Codon kernels](../../../docs/how-to/develop/build-codon-kernels.md#run-the-codon-experiments) covers the host Codon install, the gates and the tests.
