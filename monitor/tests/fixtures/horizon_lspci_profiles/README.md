# Horizon LSPCI profile dumps

Canonical queue-class `lspci` captures for Horizon platforms.
Vendored for inventory / identity reference (roofline peak allowlist) and future shm validate if needed.

| File | Shape (from capture)                                                         |
| ---- | ---------------------------------------------------------------------------- |
| `gb` | NVIDIA Grace/GB200-class bridges; 4x `GB100 [HGX GB200]`; CX8 IB; CX6 Lx Eth |

This directory (in git) is the authoritative path.
Do not depend on workspace-root `Horizon-LSPCI profiles/`.

To refresh a profile, run `lspci` (or `lspci -nn`) on a representative Horizon node of each class and replace the matching basename.
Keep basename `gb` stable.
