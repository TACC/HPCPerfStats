# Lonestar6 LSPCI profile dumps

Canonical queue-class `lspci` captures for Lonestar6 platforms. Vendored for
inventory / identity reference (roofline peak allowlist) and future shm validate
profile wiring if needed.

| File | Shape (from capture) |
|------|----------------------|
| `genoa` | AMD Genoa CPU node; Matrox VGA; ConnectX-6; no NVIDIA |
| `a100` | AMD Milan + 3× GA100 [A100 PCIe 40GB] `[10de:20f1]`; ConnectX-6 |

**Authoritative path:** this directory (in git). Do not depend on workspace-root
`LS6-LSPCI profiles/`.

**Refresh:** on a representative Lonestar6 node of each class, run `lspci -nn`
and replace the matching basename file. Keep basenames stable.
