# Horizon LSPCI profile dumps

Canonical queue-class `lspci` captures for Horizon platforms. Vendored for
inventory / identity reference (roofline peak allowlist) and future shm validate
if needed.

| File | Shape (from capture) |
|------|----------------------|
| `gb` | NVIDIA Grace/GB200-class bridges; **4×** `GB100 [HGX GB200]`; CX8 IB; CX6 Lx Eth |

**Authoritative path:** this directory (in git). Do not depend on workspace-root
`Horizon-LSPCI profiles/`.

**Refresh:** on a representative Horizon node of each class, run `lspci` (or
`lspci -nn`) and replace the matching basename. Keep basename `gb` stable.
