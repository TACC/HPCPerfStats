# Lonestar6 LSPCI profile dumps

Canonical queue-class `lspci` captures for Lonestar6 platforms.
Vendored for inventory / identity reference (roofline peak allowlist) and future shm validate profile wiring if needed.

| File    | Shape (from capture)                                                       |
| ------- | -------------------------------------------------------------------------- |
| `genoa` | AMD Genoa CPU node; Matrox VGA; ConnectX-6; no NVIDIA                      |
| `milan` | AMD Milan CPU node; Matrox VGA; ConnectX-6; no NVIDIA (CPU-only vs `a100`) |
| `a100`  | AMD Milan + 3x GA100 [A100 PCIe 40GB] `[10de:20f1]`; ConnectX-6            |

This directory (in git) is the authoritative path.
Do not depend on workspace-root `LS6-LSPCI profiles/`.

To refresh a profile, run `lspci -nn` on a representative Lonestar6 node of each class and replace the matching basename file.
Keep basenames stable.
