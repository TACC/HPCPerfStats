# Stampede3 LSPCI profile dumps

Canonical queue-class `lspci` captures for Stampede3 platforms used by
shm validate profile identity (`--profile` / `tacc_system_profiles.py`).

| File | Queue / class |
|------|----------------|
| `skx` | Sky Lake-E + OPA100 |
| `clx` | Cascade Lake (Sky Lake-E lspci) + CX6 IB + CX5 Eth; no NVIDIA |
| `icx` | Ice Lake + OPA100 |
| `spr` | SPR-class + Cornelis CN5000 |
| `h100` | H100 + ConnectX-7 + OPA |
| `pvc` | Intel PVC Max 1550 + OPA |
| `amd-rtx` | AMD Turin + NVIDIA GB202 + IB + OPA |

**Authoritative path:** this directory (in git). Do not depend on workspace-root
`Stampede3-LSPCI profiles/`.

**Refresh:** on a representative node of each queue, run `lspci` (or `lspci -nn`)
and replace the matching basename file. Keep basenames stable.
